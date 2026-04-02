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
        payload = mr_sync._map_material_request(full_mr)

        assert payload["omni_id"] == str(full_mr.id)
        assert payload["status"] == "issued"
        assert payload["request_type"] == "ISSUE"
        assert payload["requested_by_email"] == full_mr.requested_by.email
        assert payload["ticket_crm_id"] == str(full_mr.ticket_id)
        assert payload["schedule_date"]
        assert "priority" not in payload
        assert "material_request" not in payload
        assert "actors" not in payload
        assert "links" not in payload
        assert len(payload["items"]) == 1
        assert payload["items"][0]["quantity"] == 5
        assert payload["items"][0]["from_warehouse_code"] == "ERP-WH-001"
        assert "line_id" not in payload["items"][0]
        assert "item_name" not in payload["items"][0]
        assert "to_warehouse_code" not in payload["items"][0]
        assert "notes" not in payload["items"][0]

    def test_status_is_issued_for_approved_status(self, mr_sync, full_mr, db_session):
        full_mr.status = MaterialRequestStatus.approved
        db_session.commit()
        db_session.refresh(full_mr)

        payload = mr_sync._map_material_request(full_mr)

        assert payload["status"] == "issued"

    def test_issue_payload_omits_destination_warehouses(self, mr_sync, full_mr, destination_location, db_session):
        full_mr.destination_location_id = destination_location.id
        db_session.commit()
        db_session.refresh(full_mr)

        payload = mr_sync._map_material_request(full_mr)

        assert payload["request_type"] == "ISSUE"
        assert payload["items"][0]["from_warehouse_code"] == "ERP-WH-001"
        assert "to_warehouse_code" not in payload["items"][0]

    def test_ticket_fields_included(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr)

        if full_mr.ticket_id:
            assert payload["ticket_crm_id"] == str(full_mr.ticket_id)

    def test_project_fields_not_sent(self, mr_sync, db_session, full_mr, project):
        full_mr.project_id = project.id
        db_session.commit()

        payload = mr_sync._map_material_request(full_mr)
        assert "project_omni_id" not in payload

    def test_requested_by_email(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr)
        if full_mr.requested_by:
            assert payload["requested_by_email"] == full_mr.requested_by.email

    def test_item_mapping(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr)
        item = payload["items"][0]
        assert "item_code" in item
        assert "quantity" in item
        assert "uom" in item
        assert "from_warehouse_code" in item


class TestSyncMaterialRequest:
    def test_success(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.return_value = {
            "request_id": "MAT-REQ-2026-00001",
            "request_number": "MAT-REQ-2026-00001",
            "omni_id": str(full_mr.id),
            "status": "ISSUED",
        }

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is True
        assert result.erp_material_request_id == "MAT-REQ-2026-00001"
        assert full_mr.erp_material_request_id == "MAT-REQ-2026-00001"
        mock_client.push_material_request.assert_called_once()

    def test_success_on_identical_resend_200(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.return_value = {
            "request_id": "MAT-REQ-2026-00001",
            "request_number": "MAT-REQ-2026-00001",
            "omni_id": str(full_mr.id),
            "status": "ISSUED",
        }

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is True
        assert result.erp_material_request_id == "MAT-REQ-2026-00001"

    def test_idempotency_key_format(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.return_value = {"request_id": "X"}

        mr_sync.sync_material_request(full_mr)

        call_kwargs = mock_client.push_material_request.call_args
        assert call_kwargs.kwargs.get("idempotency_key") == f"mr-{full_mr.id}-approve-v1"

    def test_does_not_overwrite_existing_erp_id(self, mr_sync, mock_client, full_mr, db_session):
        full_mr.erp_material_request_id = "ALREADY-SET"
        db_session.commit()

        mock_client.push_material_request.return_value = {
            "request_id": "NEW-ID",
        }

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is True
        assert full_mr.erp_material_request_id == "ALREADY-SET"

    def test_409_conflict_is_failure(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.side_effect = DotMacERPError(
            "API error (409): payload conflict",
            status_code=409,
            response={"request_id": "MAT-REPLAY-01"},
        )

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is False
        assert result.error is not None
        assert "409" in result.error

    def test_handles_api_validation_error_without_retry(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.side_effect = DotMacERPError(
            "API error (422): items required", status_code=422
        )

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is False
        assert result.error is not None
        assert "422" in result.error
        assert result.error_type == "DotMacERPError"

    def test_retries_when_2xx_missing_request_id(self, mr_sync, mock_client, full_mr):
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
