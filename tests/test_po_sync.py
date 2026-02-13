"""Tests for PO sync: vendor quote → work order → ERP purchase order."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.dotmac_erp.po_sync import DotMacERPPurchaseOrderSync

# ---------------------------------------------------------------------------
# Helpers to build lightweight model stubs without a real DB session
# ---------------------------------------------------------------------------


def _make_vendor(erp_id: str | None = "SUP-001", code: str | None = "ACME") -> MagicMock:
    v = MagicMock()
    v.id = uuid.uuid4()
    v.name = "Acme Contractors"
    v.code = code
    v.erp_id = erp_id
    v.contact_email = "vendor@acme.test"
    return v


def _make_line_item(**overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "item_type": "labor",
        "description": "Fiber splicing",
        "cable_type": "ADSS 24F",
        "fiber_count": 24,
        "splice_count": 48,
        "quantity": Decimal("1.000"),
        "unit_price": Decimal("75000.00"),
        "amount": Decimal("75000.00"),
        "notes": None,
        "is_active": True,
    }
    defaults.update(overrides)
    li = MagicMock()
    for k, val in defaults.items():
        setattr(li, k, val)
    return li


def _make_quote(vendor, line_items=None) -> MagicMock:
    q = MagicMock()
    q.id = uuid.uuid4()
    q.vendor_id = vendor.id
    q.vendor = vendor
    q.status = MagicMock()
    q.status.value = "approved"
    q.currency = "NGN"
    q.subtotal = Decimal("150000.00")
    q.tax_total = Decimal("11250.00")
    q.total = Decimal("161250.00")
    q.reviewed_at = datetime(2026, 2, 13, 10, 30, tzinfo=UTC)
    reviewer = MagicMock()
    reviewer.email = "admin@dotmac.io"
    q.reviewed_by = reviewer
    q.line_items = line_items if line_items is not None else [_make_line_item()]
    return q


def _make_project():
    proj = MagicMock()
    proj.id = uuid.uuid4()
    proj.code = "PRJ-001"
    proj.name = "Fiber Build - Phase 2"
    return proj


def _make_work_order(project=None) -> MagicMock:
    wo = MagicMock()
    wo.id = uuid.uuid4()
    wo.title = "Vendor Quote WO - PRJ-001 - Acme Contractors"
    wo.project_id = project.id if project else None
    wo.project = project
    wo.metadata_ = {"automation_source": "automation.create_work_order"}
    return wo


# ---------------------------------------------------------------------------
# Tests for _map_purchase_order payload structure
# ---------------------------------------------------------------------------


class TestMapPurchaseOrder:
    def test_payload_structure(self):
        vendor = _make_vendor(erp_id="SUP-123", code="ACME")
        project = _make_project()
        quote = _make_quote(vendor)
        wo = _make_work_order(project)

        client = MagicMock()
        session = MagicMock()
        sync = DotMacERPPurchaseOrderSync(client, session)
        payload = sync._map_purchase_order(wo, quote)

        assert payload["omni_work_order_id"] == str(wo.id)
        assert payload["omni_quote_id"] == str(quote.id)
        assert payload["vendor_erp_id"] == "SUP-123"
        assert payload["vendor_name"] == "Acme Contractors"
        assert payload["vendor_code"] == "ACME"
        assert payload["currency"] == "NGN"
        assert payload["subtotal"] == "150000.00"
        assert payload["tax_total"] == "11250.00"
        assert payload["total"] == "161250.00"
        assert payload["title"] == wo.title
        assert payload["omni_project_id"] == str(project.id)
        assert payload["project_code"] == "PRJ-001"
        assert payload["project_name"] == "Fiber Build - Phase 2"
        assert payload["approved_at"] is not None
        assert payload["approved_by_email"] == "admin@dotmac.io"

    def test_items_mapping(self):
        vendor = _make_vendor()
        li1 = _make_line_item(item_type="labor", cable_type="ADSS 24F", fiber_count=24, splice_count=48)
        li2 = _make_line_item(
            item_type="material",
            description="Drop cable",
            cable_type=None,
            fiber_count=None,
            splice_count=None,
            quantity=Decimal("500.000"),
            unit_price=Decimal("150.00"),
            amount=Decimal("75000.00"),
        )
        inactive = _make_line_item(is_active=False)
        quote = _make_quote(vendor, [li1, li2, inactive])
        wo = _make_work_order()

        client = MagicMock()
        session = MagicMock()
        sync = DotMacERPPurchaseOrderSync(client, session)
        payload = sync._map_purchase_order(wo, quote)

        assert len(payload["items"]) == 2  # inactive excluded
        labor_item = payload["items"][0]
        assert labor_item["item_type"] == "labor"
        assert labor_item["cable_type"] == "ADSS 24F"
        assert labor_item["fiber_count"] == 24

        material_item = payload["items"][1]
        assert material_item["item_type"] == "material"
        assert "cable_type" not in material_item
        assert "fiber_count" not in material_item

    def test_no_project(self):
        vendor = _make_vendor()
        quote = _make_quote(vendor)
        wo = _make_work_order(project=None)

        client = MagicMock()
        session = MagicMock()
        sync = DotMacERPPurchaseOrderSync(client, session)
        payload = sync._map_purchase_order(wo, quote)

        assert "omni_project_id" not in payload
        assert "project_code" not in payload

    def test_no_vendor_code(self):
        vendor = _make_vendor(code=None)
        quote = _make_quote(vendor)
        wo = _make_work_order()

        client = MagicMock()
        session = MagicMock()
        sync = DotMacERPPurchaseOrderSync(client, session)
        payload = sync._map_purchase_order(wo, quote)

        assert "vendor_code" not in payload


# ---------------------------------------------------------------------------
# Tests for sync_purchase_order
# ---------------------------------------------------------------------------


class TestSyncPurchaseOrder:
    def test_success(self):
        vendor = _make_vendor(erp_id="SUP-001")
        quote = _make_quote(vendor)
        wo = _make_work_order()

        client = MagicMock()
        client.create_purchase_order.return_value = {"purchase_order_id": "PO-2026-00045", "status": "draft"}
        session = MagicMock()

        sync = DotMacERPPurchaseOrderSync(client, session)
        result = sync.sync_purchase_order(wo, quote)

        assert result.success is True
        assert result.erp_po_id == "PO-2026-00045"
        assert wo.metadata_["erp_po_id"] == "PO-2026-00045"
        session.commit.assert_called_once()
        client.create_purchase_order.assert_called_once()

    def test_vendor_no_erp_id_skips(self):
        vendor = _make_vendor(erp_id=None)
        quote = _make_quote(vendor)
        wo = _make_work_order()

        client = MagicMock()
        session = MagicMock()

        sync = DotMacERPPurchaseOrderSync(client, session)
        result = sync.sync_purchase_order(wo, quote)

        assert result.success is False
        assert result.error_type == "vendor_no_erp_id"
        client.create_purchase_order.assert_not_called()

    def test_client_error(self):
        vendor = _make_vendor(erp_id="SUP-001")
        quote = _make_quote(vendor)
        wo = _make_work_order()

        client = MagicMock()
        client.create_purchase_order.side_effect = RuntimeError("Connection refused")
        session = MagicMock()

        sync = DotMacERPPurchaseOrderSync(client, session)
        result = sync.sync_purchase_order(wo, quote)

        assert result.success is False
        assert "Connection refused" in result.error
        assert result.error_type == "RuntimeError"

    def test_preserves_existing_metadata(self):
        vendor = _make_vendor(erp_id="SUP-001")
        quote = _make_quote(vendor)
        wo = _make_work_order()
        wo.metadata_ = {"automation_source": "test", "existing_key": "keep_me"}

        client = MagicMock()
        client.create_purchase_order.return_value = {"purchase_order_id": "PO-99"}
        session = MagicMock()

        sync = DotMacERPPurchaseOrderSync(client, session)
        sync.sync_purchase_order(wo, quote)

        assert wo.metadata_["existing_key"] == "keep_me"
        assert wo.metadata_["erp_po_id"] == "PO-99"


# ---------------------------------------------------------------------------
# Test trigger wiring in _execute_create_work_order
# ---------------------------------------------------------------------------


class TestAutomationTrigger:
    @patch("app.services.automation_actions.sync_purchase_order_to_erp", create=True)
    def test_queues_po_sync_when_quote_id_present(self, mock_task):
        """Verify that _execute_create_work_order fires the Celery task when quote_id is in event payload."""
        from app.services.automation_actions import _execute_create_work_order

        mock_apply_async = MagicMock()
        mock_task.apply_async = mock_apply_async

        db = MagicMock()
        wo_instance = MagicMock()
        wo_instance.id = uuid.uuid4()
        wo_instance.metadata_ = {}
        db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.first.return_value = None

        event = MagicMock()
        event.ticket_id = None
        event.project_id = uuid.uuid4()
        event.event_type.value = "vendor_quote.approved"
        event.payload = {"quote_id": str(uuid.uuid4())}

        with (
            patch("app.services.automation_actions.WorkOrder", return_value=wo_instance),
            patch("app.tasks.integrations.sync_purchase_order_to_erp") as patched_task,
        ):
            patched_task.apply_async = mock_apply_async
            _execute_create_work_order(db, {}, event)

        mock_apply_async.assert_called_once()
        call_args = mock_apply_async.call_args
        assert len(call_args[1]["args"]) == 2 or len(call_args[0][0]) == 2
