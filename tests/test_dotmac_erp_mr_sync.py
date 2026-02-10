"""Tests for DotMac ERP material request sync (push to ERP)."""

from unittest.mock import MagicMock

import pytest

from app.models.material_request import (
    MaterialRequestStatus,
)
from app.services.dotmac_erp.client import DotMacERPError
from app.services.dotmac_erp.material_request_sync import (
    DotMacERPMaterialRequestSync,
    MaterialRequestSyncResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_client():
    return MagicMock()


@pytest.fixture()
def mr_sync(mock_client, db_session):
    return DotMacERPMaterialRequestSync(client=mock_client, session=db_session)


@pytest.fixture()
def full_mr(db_session, material_request_with_item):
    """Material request with items, ready for sync."""
    mr = material_request_with_item
    mr.status = MaterialRequestStatus.approved
    mr.number = "MR-2026-00001"
    db_session.commit()
    db_session.refresh(mr)
    return mr


# ---------------------------------------------------------------------------
# MaterialRequestSyncResult
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Payload mapping
# ---------------------------------------------------------------------------

class TestMapMaterialRequest:
    def test_basic_payload(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr)

        assert payload["omni_id"] == str(full_mr.id)
        assert payload["number"] == "MR-2026-00001"
        assert payload["status"] == "approved"
        assert payload["priority"] == full_mr.priority.value
        assert len(payload["items"]) == 1
        assert payload["items"][0]["quantity"] == 5

    def test_ticket_fields_included(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr)

        if full_mr.ticket_id:
            assert payload["ticket_omni_id"] == str(full_mr.ticket_id)

    def test_project_fields_included(self, mr_sync, db_session, full_mr, project):
        full_mr.project_id = project.id
        db_session.commit()

        payload = mr_sync._map_material_request(full_mr)
        assert payload["project_omni_id"] == str(project.id)

    def test_requested_by_email(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr)
        if full_mr.requested_by:
            assert "requested_by_email" in payload

    def test_item_mapping(self, mr_sync, full_mr):
        payload = mr_sync._map_material_request(full_mr)
        item = payload["items"][0]
        assert "item_code" in item
        assert "item_name" in item
        assert "quantity" in item


# ---------------------------------------------------------------------------
# sync_material_request
# ---------------------------------------------------------------------------

class TestSyncMaterialRequest:
    def test_success(self, mr_sync, mock_client, full_mr, db_session):
        mock_client.push_material_request.return_value = {
            "material_request_id": "MAT-REQ-2026-00001",
            "omni_id": str(full_mr.id),
            "status": "pending",
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
        assert call_kwargs.kwargs.get("idempotency_key") == f"mr-{full_mr.id}"

    def test_does_not_overwrite_existing_erp_id(self, mr_sync, mock_client, full_mr, db_session):
        full_mr.erp_material_request_id = "ALREADY-SET"
        db_session.commit()

        mock_client.push_material_request.return_value = {
            "material_request_id": "NEW-ID",
        }

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is True
        # Should not overwrite existing ID
        assert full_mr.erp_material_request_id == "ALREADY-SET"

    def test_handles_api_error(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.side_effect = DotMacERPError(
            "API error (422): items required", status_code=422
        )

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is False
        assert result.error is not None
        assert "422" in result.error
        assert result.error_type == "DotMacERPError"

    def test_handles_connection_error(self, mr_sync, mock_client, full_mr):
        mock_client.push_material_request.side_effect = ConnectionError("timeout")

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is False
        assert result.error is not None

    def test_handles_none_response(self, mr_sync, mock_client, full_mr, db_session):
        mock_client.push_material_request.return_value = None

        result = mr_sync.sync_material_request(full_mr)

        assert result.success is True
        assert result.erp_material_request_id is None


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

class TestFactory:
    def test_raises_when_not_configured(self, db_session):
        from unittest.mock import patch

        from app.services.dotmac_erp.material_request_sync import dotmac_erp_material_request_sync

        with (
            patch("app.services.settings_spec.resolve_value", return_value=None),
            pytest.raises(ValueError, match="not configured"),
        ):
            dotmac_erp_material_request_sync(db_session)
