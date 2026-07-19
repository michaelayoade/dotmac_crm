"""Status-transition tests for the PO and purchase-invoice ERP push tasks.

The push intent rows (WorkOrder for POs, VendorPurchaseInvoice for invoices)
must persist a sweepable erp_sync_status marker on every outcome — a terminal
failure may never be just a log line.
"""

from unittest.mock import MagicMock

import pytest

from app.models.vendor import (
    InstallationProject,
    InstallationProjectStatus,
    ProjectQuote,
    ProjectQuoteStatus,
    Vendor,
    VendorPurchaseInvoice,
)
from app.models.workforce import WorkOrder
from app.services.dotmac_erp.client import DotMacERPTransientError
from app.services.dotmac_erp.po_sync import PurchaseOrderSyncResult
from app.services.dotmac_erp.purchase_invoice_sync import PurchaseInvoiceSyncResult
from app.services.dotmac_erp.push_redrive import (
    ERP_SYNC_FAILED,
    ERP_SYNC_NOT_CONFIGURED,
    ERP_SYNC_PENDING,
    ERP_SYNC_RETRYING,
    ERP_SYNC_SYNCED,
)


@pytest.fixture()
def vendor(db_session):
    vendor = Vendor(name="FiberWorks", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    return vendor


@pytest.fixture()
def installation_project(db_session, project, vendor):
    ip = InstallationProject(
        project_id=project.id,
        assigned_vendor_id=vendor.id,
        status=InstallationProjectStatus.in_progress,
    )
    db_session.add(ip)
    db_session.commit()
    return ip


@pytest.fixture()
def quote(db_session, installation_project, vendor):
    quote = ProjectQuote(
        project_id=installation_project.id,
        vendor_id=vendor.id,
        status=ProjectQuoteStatus.approved,
    )
    db_session.add(quote)
    db_session.commit()
    return quote


@pytest.fixture()
def work_order(db_session):
    wo = WorkOrder(title="Install from approved quote")
    db_session.add(wo)
    db_session.commit()
    return wo


@pytest.fixture()
def invoice(db_session, installation_project, vendor):
    invoice = VendorPurchaseInvoice(
        project_id=installation_project.id,
        vendor_id=vendor.id,
        erp_purchase_order_id="EPO-1",
    )
    db_session.add(invoice)
    db_session.commit()
    return invoice


def _po_task_env(monkeypatch, db_session, result=None, side_effect=None, factory_side_effect=None):
    from app.services.dotmac_erp import po_sync as po_sync_module

    monkeypatch.setattr("app.tasks.integrations.SessionLocal", lambda: db_session)
    if factory_side_effect is not None:
        monkeypatch.setattr(
            po_sync_module, "dotmac_erp_purchase_order_sync", MagicMock(side_effect=factory_side_effect)
        )
        return None
    mock_service = MagicMock()
    if side_effect is not None:
        mock_service.sync_purchase_order.side_effect = side_effect
    else:
        mock_service.sync_purchase_order.return_value = result
    monkeypatch.setattr(po_sync_module, "dotmac_erp_purchase_order_sync", lambda session: mock_service)
    return mock_service


class TestPurchaseOrderSyncTaskStatus:
    def test_success_marks_synced_and_records_quote_id(self, db_session, work_order, quote, monkeypatch):
        from app.tasks.integrations import sync_purchase_order_to_erp

        wo_id, quote_id = work_order.id, quote.id
        _po_task_env(
            monkeypatch,
            db_session,
            result=PurchaseOrderSyncResult(success=True, work_order_id=str(wo_id), erp_po_id="PO-0001"),
        )

        result = sync_purchase_order_to_erp.run(str(wo_id), str(quote_id))

        wo = db_session.get(WorkOrder, wo_id)
        assert result["success"] is True
        assert wo.erp_sync_status == ERP_SYNC_SYNCED
        assert wo.erp_sync_error is None
        assert wo.erp_synced_at is not None
        assert wo.erp_po_quote_id == quote_id

    def test_terminal_failure_persists_failed_and_error(self, db_session, work_order, quote, monkeypatch):
        from app.tasks.integrations import sync_purchase_order_to_erp

        wo_id, quote_id = work_order.id, quote.id
        _po_task_env(
            monkeypatch,
            db_session,
            result=PurchaseOrderSyncResult(
                success=False,
                work_order_id=str(wo_id),
                error="API error (422): items required",
                error_type="DotMacERPError",
            ),
        )

        result = sync_purchase_order_to_erp.run(str(wo_id), str(quote_id))

        wo = db_session.get(WorkOrder, wo_id)
        assert result["success"] is False
        assert wo.erp_sync_status == ERP_SYNC_FAILED
        assert "422" in (wo.erp_sync_error or "")
        assert wo.erp_po_quote_id == quote_id  # sweep can re-drive with same args

    def test_transient_error_marks_retrying_and_reraises(self, db_session, work_order, quote, monkeypatch):
        from app.tasks.integrations import sync_purchase_order_to_erp

        wo_id, quote_id = work_order.id, quote.id
        _po_task_env(monkeypatch, db_session, side_effect=DotMacERPTransientError("ERP timeout"))

        with pytest.raises(DotMacERPTransientError):
            sync_purchase_order_to_erp.run(str(wo_id), str(quote_id))

        wo = db_session.get(WorkOrder, wo_id)
        assert wo.erp_sync_status == ERP_SYNC_RETRYING
        assert "ERP timeout" in (wo.erp_sync_error or "")

    def test_not_configured_marks_not_configured(self, db_session, work_order, quote, monkeypatch):
        from app.tasks.integrations import sync_purchase_order_to_erp

        wo_id, quote_id = work_order.id, quote.id
        _po_task_env(monkeypatch, db_session, factory_side_effect=ValueError("DotMac ERP is not configured"))

        result = sync_purchase_order_to_erp.run(str(wo_id), str(quote_id))

        wo = db_session.get(WorkOrder, wo_id)
        assert result["error_type"] == "not_configured"
        assert wo.erp_sync_status == ERP_SYNC_NOT_CONFIGURED
        assert "not configured" in (wo.erp_sync_error or "")


def _invoice_task_env(monkeypatch, db_session, result=None, side_effect=None, factory_side_effect=None):
    import app.services.dotmac_erp as dotmac_erp_pkg

    monkeypatch.setattr("app.tasks.integrations.SessionLocal", lambda: db_session)
    if factory_side_effect is not None:
        monkeypatch.setattr(
            dotmac_erp_pkg, "dotmac_erp_purchase_invoice_sync", MagicMock(side_effect=factory_side_effect)
        )
        return None
    mock_service = MagicMock()
    if side_effect is not None:
        mock_service.sync_purchase_invoice.side_effect = side_effect
    else:
        mock_service.sync_purchase_invoice.return_value = result
    monkeypatch.setattr(dotmac_erp_pkg, "dotmac_erp_purchase_invoice_sync", lambda session: mock_service)
    return mock_service


class TestPurchaseInvoiceSyncTaskStatus:
    def test_success_marks_synced(self, db_session, invoice, monkeypatch):
        from app.tasks.integrations import sync_purchase_invoice_to_erp

        invoice_id = invoice.id
        _invoice_task_env(
            monkeypatch,
            db_session,
            result=PurchaseInvoiceSyncResult(
                success=True, invoice_id=str(invoice_id), erp_purchase_invoice_id="PINV-0001"
            ),
        )

        result = sync_purchase_invoice_to_erp.run(str(invoice_id))

        row = db_session.get(VendorPurchaseInvoice, invoice_id)
        assert result["success"] is True
        assert row.erp_sync_status == ERP_SYNC_SYNCED
        assert row.erp_sync_error is None
        assert row.erp_synced_at is not None

    def test_terminal_failure_persists_failed_and_error(self, db_session, invoice, monkeypatch):
        from app.tasks.integrations import sync_purchase_invoice_to_erp

        invoice_id = invoice.id
        _invoice_task_env(
            monkeypatch,
            db_session,
            result=PurchaseInvoiceSyncResult(
                success=False,
                invoice_id=str(invoice_id),
                error="API error (400): bad vendor",
                error_type="DotMacERPError",
            ),
        )

        result = sync_purchase_invoice_to_erp.run(str(invoice_id))

        row = db_session.get(VendorPurchaseInvoice, invoice_id)
        assert result["success"] is False
        assert row.erp_sync_status == ERP_SYNC_FAILED
        assert "400" in (row.erp_sync_error or "")

    def test_pending_prerequisite_stays_pending_for_sweep(self, db_session, invoice, monkeypatch):
        from app.tasks.integrations import sync_purchase_invoice_to_erp

        invoice_id = invoice.id
        _invoice_task_env(
            monkeypatch,
            db_session,
            result=PurchaseInvoiceSyncResult(
                success=False,
                invoice_id=str(invoice_id),
                error="Waiting for PO sync",
                error_type="PendingPrerequisite",
            ),
        )

        sync_purchase_invoice_to_erp.run(str(invoice_id))

        row = db_session.get(VendorPurchaseInvoice, invoice_id)
        assert row.erp_sync_status == ERP_SYNC_PENDING
        assert "Waiting for PO sync" in (row.erp_sync_error or "")

    def test_transient_error_marks_retrying_and_reraises(self, db_session, invoice, monkeypatch):
        from app.tasks.integrations import sync_purchase_invoice_to_erp

        invoice_id = invoice.id
        _invoice_task_env(monkeypatch, db_session, side_effect=DotMacERPTransientError("ERP down"))

        with pytest.raises(DotMacERPTransientError):
            sync_purchase_invoice_to_erp.run(str(invoice_id))

        row = db_session.get(VendorPurchaseInvoice, invoice_id)
        assert row.erp_sync_status == ERP_SYNC_RETRYING
        assert "ERP down" in (row.erp_sync_error or "")

    def test_not_configured_marks_not_configured(self, db_session, invoice, monkeypatch):
        from app.tasks.integrations import sync_purchase_invoice_to_erp

        invoice_id = invoice.id
        _invoice_task_env(monkeypatch, db_session, factory_side_effect=ValueError("DotMac ERP is not configured"))

        result = sync_purchase_invoice_to_erp.run(str(invoice_id))

        row = db_session.get(VendorPurchaseInvoice, invoice_id)
        assert result["error_type"] == "not_configured"
        assert row.erp_sync_status == ERP_SYNC_NOT_CONFIGURED
