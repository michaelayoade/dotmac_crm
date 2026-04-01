"""Tests for DotMac ERP material request sync (push to ERP)."""

from unittest.mock import MagicMock

import pytest

from app.models.inventory import InventoryLocation
from app.models.material_request import (
    MaterialRequestStatus,
)
from app.services.dotmac_erp.client import DotMacERPError, DotMacERPTransientError
from app.services.dotmac_erp.material_request_sync import (
    DotMacERPMaterialRequestSync,
    MaterialRequestSyncResult,
)


@pytest.fixture()
def mock_client():
    return MagicMock()


@pytest.fixture()
def mr_sync(mock_client, db_session):
    return DotMacERPMaterialRequestSync(client=mock_client, session=db_session)


@pytest.fixture()
def inventory_location(db_session):
    location = InventoryLocation(name="Main Warehouse", code="ERP-WH-001")
    db_session.add(location)
    db_session.commit()
    db_session.refresh(location)
    return location


@pytest.fixture()
def destination_location(db_session):
    location = InventoryLocation(name="Second Warehouse", code="ERP-WH-002")
    db_session.add(location)
    db_session.commit()
    db_session.refresh(location)
    return location


@pytest.fixture()
def full_mr(db_session, material_request_with_item, inventory_location):
    """Material request with items, ready for sync."""
    mr = material_request_with_item
    mr.status = MaterialRequestStatus.issued
    mr.number = "MR-2026-00001"
    mr.source_location_id = inventory_location.id
    db_session.commit()
    db_session.refresh(mr)
    return mr


class TestMaterialRequestSyncResult:
    def test_defaults(self):
        r = MaterialRequestSyncResult()
        assert r.success is False
        assert r.material_request_id is None
        assert r.erp_material_request_id is None
        assert r.error is None

    def test_success_result(self):
        r = MaterialRequestSyncResult(
            success=True,
            material_request_id="abc",
            erp_material_request_id="MAT-001",
        )
        assert r.success is True
        assert r.erp_material_request_id == "MAT-001"


class TestMapMaterialRequest:
    def test_basic_payload(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr, idempotency_key="idem-1")

        assert payload["event_type"] == "material_request.issued"
        assert payload["idempotency_key"] == "idem-1"
        assert payload["source_system"] == "crm"
        assert payload["material_request"]["omni_id"] == str(full_mr.id)
        assert payload["material_request"]["number"] == "MR-2026-00001"
        assert payload["material_request"]["status"] == "issued"
        assert payload["material_request"]["priority"] == full_mr.priority.value
        assert payload["material_request"]["request_type"] == "ISSUE"
        assert payload["material_request"]["default_from_warehouse_code"] == "ERP-WH-001"
        assert payload["material_request"]["default_to_warehouse_code"] is None
        assert len(payload["items"]) == 1
        assert payload["items"][0]["quantity"] == 5

    def test_event_type_is_approved_for_approved_status(self, mr_sync, full_mr, db_session):
        full_mr.status = MaterialRequestStatus.approved
        db_session.commit()
        db_session.refresh(full_mr)

        payload = mr_sync._map_material_request(full_mr, idempotency_key="idem-1")

        assert payload["event_type"] == "material_request.approved"

    def test_transfer_payload_has_destination_warehouses(self, mr_sync, full_mr, destination_location, db_session):
        full_mr.destination_location_id = destination_location.id
        db_session.commit()
        db_session.refresh(full_mr)

        payload = mr_sync._map_material_request(full_mr, idempotency_key="idem-1")

        assert payload["material_request"]["request_type"] == "TRANSFER"
        assert payload["material_request"]["default_to_warehouse_code"] == "ERP-WH-002"
        assert payload["items"][0]["from_warehouse_code"] == "ERP-WH-001"
        assert payload["items"][0]["to_warehouse_code"] == "ERP-WH-002"

    def test_ticket_fields_included(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr, idempotency_key="idem-1")

        if full_mr.ticket_id:
            assert payload["links"]["ticket_omni_id"] == str(full_mr.ticket_id)

    def test_project_fields_included(self, mr_sync, db_session, full_mr, project):
        full_mr.project_id = project.id
        db_session.commit()

        payload = mr_sync._map_material_request(full_mr, idempotency_key="idem-1")
        assert payload["links"]["project_omni_id"] == str(project.id)

    def test_requested_by_email(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr, idempotency_key="idem-1")
        if full_mr.requested_by:
            assert payload["actors"]["requested_by_email"] == full_mr.requested_by.email

    def test_item_mapping(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr, idempotency_key="idem-1")
        item = payload["items"][0]
        assert "item_code" in item
        assert "item_name" in item
        assert "quantity" in item
        assert "uom" in item
        assert "line_id" in item


class TestSyncMaterialRequest:
    def test_success(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.return_value = {
            "material_request_id": "MAT-REQ-2026-00001",
            "omni_id": str(full_mr.id),
            "status": "SYNCED",
        }

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is True
        assert result.erp_material_request_id == "MAT-REQ-2026-00001"
        assert full_mr.erp_material_request_id == "MAT-REQ-2026-00001"
        mock_client.push_material_request.assert_called_once()

    def test_idempotency_key_format(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.return_value = {"material_request_id": "X"}

        mr_sync.sync_material_request(full_mr)

        call_kwargs = mock_client.push_material_request.call_args
        assert call_kwargs.kwargs.get("idempotency_key") == f"mr-{full_mr.id}-approve-v1"

    def test_does_not_overwrite_existing_erp_id(self, mr_sync, mock_client, full_mr, db_session):
        full_mr.erp_material_request_id = "ALREADY-SET"
        db_session.commit()

        mock_client.push_material_request.return_value = {
            "material_request_id": "NEW-ID",
        }

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is True
        assert full_mr.erp_material_request_id == "ALREADY-SET"

    def test_treats_409_idempotent_replay_as_success(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.side_effect = DotMacERPError(
            "API error (409): duplicate",
            status_code=409,
            response={"material_request_id": "MAT-REPLAY-01"},
        )

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is True
        assert result.erp_material_request_id == "MAT-REPLAY-01"

    def test_handles_api_validation_error_without_retry(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.side_effect = DotMacERPError(
            "API error (422): items required", status_code=422
        )

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is False
        assert result.error is not None
        assert "422" in result.error
        assert result.error_type == "DotMacERPError"

    def test_retries_when_2xx_missing_material_request_id(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.return_value = {"accepted": True, "sync_status": "QUEUED"}

        with pytest.raises(DotMacERPTransientError):
            mr_sync.sync_material_request(full_mr)

    def test_retries_transient_5xx(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.side_effect = DotMacERPError(
            "API error (503): service unavailable",
            status_code=503,
        )

        with pytest.raises(DotMacERPTransientError):
            mr_sync.sync_material_request(full_mr)

    def test_retries_connection_error(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.side_effect = ConnectionError("timeout")

        with pytest.raises(DotMacERPTransientError):
            mr_sync.sync_material_request(full_mr)

    def test_blocks_draft_sync(self, mr_sync, mock_client, material_request):
        material_request.status = MaterialRequestStatus.draft

        result = mr_sync.sync_material_request(material_request)

        assert result.success is False
        assert result.status_code == 422
        mock_client.push_material_request.assert_not_called()


class TestFactory:
    def test_raises_when_not_configured(self, db_session):
        from unittest.mock import patch

        from app.services.dotmac_erp.material_request_sync import dotmac_erp_material_request_sync

        with (
            patch("app.services.settings_spec.resolve_value", return_value=None),
            pytest.raises(ValueError, match="not configured"),
        ):
            dotmac_erp_material_request_sync(db_session)
