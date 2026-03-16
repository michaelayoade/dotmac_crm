"""Tests for purchase invoice ERP sync."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from app.services.dotmac_erp.purchase_invoice_sync import DotMacERPPurchaseInvoiceSync


def _make_vendor(erp_id: str | None = "SUP-001", code: str | None = "ACME") -> MagicMock:
    vendor = MagicMock()
    vendor.id = uuid.uuid4()
    vendor.name = "Acme Contractors"
    vendor.code = code
    vendor.erp_id = erp_id
    return vendor


def _make_line_item(**overrides) -> MagicMock:
    values = {
        "item_type": "labor",
        "description": "Fiber splicing",
        "quantity": Decimal("1.000"),
        "unit_price": Decimal("75000.00"),
        "amount": Decimal("75000.00"),
        "notes": None,
        "is_active": True,
    }
    values.update(overrides)
    item = MagicMock()
    for key, value in values.items():
        setattr(item, key, value)
    return item


def _make_invoice(with_attachment: bool = False) -> MagicMock:
    vendor = _make_vendor()
    base_project = MagicMock()
    base_project.id = uuid.uuid4()
    base_project.code = "PRJ-001"
    base_project.name = "Fiber Build - Phase 2"
    project = MagicMock()
    project.id = uuid.uuid4()
    project.project = base_project
    project.approved_quote_id = uuid.uuid4()
    project.erp_purchase_order_id = "PO-2026-00001"

    reviewer = MagicMock()
    reviewer.email = "admin@dotmac.io"

    invoice = MagicMock()
    invoice.id = uuid.uuid4()
    invoice.invoice_number = "INV-0001"
    invoice.project = project
    invoice.project_id = project.id
    invoice.vendor = vendor
    invoice.erp_purchase_order_id = "PO-2026-00001"
    invoice.erp_purchase_invoice_id = None
    invoice.erp_sync_error = None
    invoice.erp_synced_at = None
    invoice.currency = "NGN"
    invoice.tax_rate_percent = Decimal("7.50")
    invoice.subtotal = Decimal("150000.00")
    invoice.tax_total = Decimal("11250.00")
    invoice.total = Decimal("161250.00")
    invoice.reviewed_at = datetime(2026, 3, 13, 12, 0, tzinfo=UTC)
    invoice.reviewed_by = reviewer
    invoice.line_items = [_make_line_item()]
    invoice.attachment_storage_key = "uploads/vendor_purchase_invoices/example/invoice.pdf" if with_attachment else None
    invoice.attachment_file_name = "invoice.pdf" if with_attachment else None
    invoice.attachment_mime_type = "application/pdf" if with_attachment else None
    return invoice


class TestPurchaseInvoiceSync:
    def test_maps_payload(self):
        client = MagicMock()
        session = MagicMock()
        sync = DotMacERPPurchaseInvoiceSync(client, session)
        invoice = _make_invoice()

        payload = sync._map_purchase_invoice(invoice)

        assert payload["crm_invoice_id"] == str(invoice.id)
        assert payload["crm_invoice_number"] == "INV-0001"
        assert payload["crm_project_id"] == str(invoice.project.project.id)
        assert payload["installation_project_id"] == str(invoice.project.id)
        assert payload["crm_quote_id"] == str(invoice.project.approved_quote_id)
        assert payload["erp_purchase_order_id"] == "PO-2026-00001"
        assert payload["vendor_erp_id"] == "SUP-001"
        assert payload["vendor_code"] == "ACME"
        assert payload["items"][0]["description"] == "Fiber splicing"

    def test_successful_sync_saves_erp_id(self):
        client = MagicMock()
        client.create_purchase_invoice.return_value = {"purchase_invoice_id": "PINV-2026-00001"}
        session = MagicMock()
        sync = DotMacERPPurchaseInvoiceSync(client, session)
        invoice = _make_invoice()

        result = sync.sync_purchase_invoice(invoice)

        assert result.success is True
        assert result.erp_purchase_invoice_id == "PINV-2026-00001"
        assert invoice.erp_purchase_invoice_id == "PINV-2026-00001"
        client.create_purchase_invoice.assert_called_once()
        session.commit.assert_called()

    def test_uploads_attachment_after_creation(self, monkeypatch):
        client = MagicMock()
        client.create_purchase_invoice.return_value = {"purchase_invoice_id": "PINV-2026-00002"}
        session = MagicMock()
        sync = DotMacERPPurchaseInvoiceSync(client, session)
        invoice = _make_invoice(with_attachment=True)

        import app.services.dotmac_erp.purchase_invoice_sync as purchase_invoice_sync_module

        monkeypatch.setattr(
            purchase_invoice_sync_module.storage,
            "get",
            lambda key: b"pdf-bytes",
        )

        result = sync.sync_purchase_invoice(invoice)

        assert result.success is True
        client.upload_purchase_invoice_attachment.assert_called_once()

    def test_missing_project_po_link_fails(self):
        client = MagicMock()
        session = MagicMock()
        sync = DotMacERPPurchaseInvoiceSync(client, session)
        invoice = _make_invoice()
        invoice.erp_purchase_order_id = None
        invoice.project.erp_purchase_order_id = None

        result = sync.sync_purchase_invoice(invoice)

        assert result.success is False
        assert "no ERP purchase order ID" in (result.error or "")
