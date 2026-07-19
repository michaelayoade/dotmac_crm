"""Tests for the ERP push re-drive sweep (failed/stale money pushes)."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.models.material_request import (
    MaterialRequest,
    MaterialRequestERPSyncStatus,
    MaterialRequestStatus,
)
from app.models.vendor import VendorPurchaseInvoice
from app.models.workforce import WorkOrder
from app.services.dotmac_erp.push_redrive import (
    ERP_SYNC_FAILED,
    ERP_SYNC_NOT_CONFIGURED,
    ERP_SYNC_PENDING,
    ERP_SYNC_RETRYING,
    ERP_SYNC_SYNCED,
    MATERIAL_REQUEST_SYNC_TASK,
    PURCHASE_INVOICE_SYNC_TASK,
    PURCHASE_ORDER_SYNC_TASK,
    redrive_failed_erp_pushes,
)

STALE = datetime.now(UTC) - timedelta(hours=2)


@pytest.fixture(autouse=True)
def _default_knobs(monkeypatch):
    """Knob values come from settings; default them (None -> built-in defaults)."""
    monkeypatch.setattr("app.services.settings_spec.resolve_value", lambda *a, **k: None)


def _backdate(db, model, row_id, when=STALE):
    db.query(model).filter(model.id == row_id).update({"updated_at": when})
    db.commit()


def _work_order(db, status, *, quote_id="new", updated_at=None):
    wo = WorkOrder(
        title="Install",
        erp_sync_status=status,
        erp_po_quote_id=uuid.uuid4() if quote_id == "new" else quote_id,
    )
    db.add(wo)
    db.commit()
    if updated_at is not None:
        _backdate(db, WorkOrder, wo.id, updated_at)
        db.refresh(wo)
    return wo


def _material_request(db, status, *, updated_at=None):
    mr = MaterialRequest(
        project_id=uuid.uuid4(),
        requested_by_person_id=uuid.uuid4(),
        status=MaterialRequestStatus.approved,
        erp_sync_status=status,
    )
    db.add(mr)
    db.commit()
    if updated_at is not None:
        _backdate(db, MaterialRequest, mr.id, updated_at)
        db.refresh(mr)
    return mr


def _invoice(db, status, *, updated_at=None):
    invoice = VendorPurchaseInvoice(
        project_id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        erp_sync_status=status,
    )
    db.add(invoice)
    db.commit()
    if updated_at is not None:
        _backdate(db, VendorPurchaseInvoice, invoice.id, updated_at)
        db.refresh(invoice)
    return invoice


class TestRedriveFailedErpPushes:
    def test_redrives_failed_rows_of_each_kind_by_task_name(self, db_session):
        mr = _material_request(db_session, MaterialRequestERPSyncStatus.failed)
        wo = _work_order(db_session, ERP_SYNC_FAILED)
        invoice = _invoice(db_session, ERP_SYNC_FAILED)
        send_task = MagicMock()

        result = redrive_failed_erp_pushes(db_session, send_task=send_task)

        assert result["total"] == 3
        assert result["material_requests"] == 1
        assert result["purchase_orders"] == 1
        assert result["purchase_invoices"] == 1
        assert result["enqueue_errors"] == 0
        calls = {c.args[0]: c.kwargs["args"] for c in send_task.call_args_list}
        assert calls[MATERIAL_REQUEST_SYNC_TASK] == [str(mr.id)]
        assert calls[PURCHASE_ORDER_SYNC_TASK] == [str(wo.id), str(wo.erp_po_quote_id)]
        assert calls[PURCHASE_INVOICE_SYNC_TASK] == [str(invoice.id)]
        # Re-driven rows are re-marked pending so the next sweep skips them
        # until the staleness threshold passes again.
        db_session.refresh(mr)
        db_session.refresh(wo)
        db_session.refresh(invoice)
        assert mr.erp_sync_status == MaterialRequestERPSyncStatus.pending
        assert wo.erp_sync_status == ERP_SYNC_PENDING
        assert invoice.erp_sync_status == ERP_SYNC_PENDING

    def test_redrives_stale_pending_and_retrying_but_not_fresh(self, db_session):
        stale_pending = _work_order(db_session, ERP_SYNC_PENDING, updated_at=STALE)
        stale_retrying = _invoice(db_session, ERP_SYNC_RETRYING, updated_at=STALE)
        fresh_pending = _work_order(db_session, ERP_SYNC_PENDING)
        send_task = MagicMock()

        result = redrive_failed_erp_pushes(db_session, send_task=send_task)

        assert result["total"] == 2
        enqueued_ids = {c.kwargs["args"][0] for c in send_task.call_args_list}
        assert str(stale_pending.id) in enqueued_ids
        assert str(stale_retrying.id) in enqueued_ids
        assert str(fresh_pending.id) not in enqueued_ids

    def test_skips_synced_not_configured_and_unmarked_rows(self, db_session):
        _work_order(db_session, ERP_SYNC_SYNCED, updated_at=STALE)
        _work_order(db_session, ERP_SYNC_NOT_CONFIGURED, updated_at=STALE)
        _work_order(db_session, None, updated_at=STALE)
        _material_request(db_session, MaterialRequestERPSyncStatus.synced, updated_at=STALE)
        _material_request(db_session, MaterialRequestERPSyncStatus.not_configured, updated_at=STALE)
        _invoice(db_session, ERP_SYNC_SYNCED, updated_at=STALE)
        send_task = MagicMock()

        result = redrive_failed_erp_pushes(db_session, send_task=send_task)

        assert result["total"] == 0
        send_task.assert_not_called()

    def test_skips_work_orders_without_recorded_quote_id(self, db_session):
        _work_order(db_session, ERP_SYNC_FAILED, quote_id=None)
        send_task = MagicMock()

        result = redrive_failed_erp_pushes(db_session, send_task=send_task)

        assert result["purchase_orders"] == 0
        send_task.assert_not_called()

    def test_respects_batch_limit_across_flows(self, db_session, monkeypatch):
        knobs = {"dotmac_erp_push_redrive_batch_limit": 2}
        monkeypatch.setattr(
            "app.services.settings_spec.resolve_value",
            lambda db, domain, key, **kw: knobs.get(key),
        )
        _material_request(db_session, MaterialRequestERPSyncStatus.failed)
        _work_order(db_session, ERP_SYNC_FAILED)
        _invoice(db_session, ERP_SYNC_FAILED)
        send_task = MagicMock()

        result = redrive_failed_erp_pushes(db_session, send_task=send_task)

        assert result["total"] == 2
        assert result["limit"] == 2
        assert send_task.call_count == 2

    def test_enqueue_failure_marks_row_failed_with_error(self, db_session):
        wo = _work_order(db_session, ERP_SYNC_FAILED)
        send_task = MagicMock(side_effect=RuntimeError("broker down"))

        result = redrive_failed_erp_pushes(db_session, send_task=send_task)

        assert result["total"] == 0
        assert result["enqueue_errors"] == 1
        db_session.refresh(wo)
        assert wo.erp_sync_status == ERP_SYNC_FAILED
        assert "broker down" in (wo.erp_sync_error or "")

    def test_stale_threshold_knob_is_honored(self, db_session, monkeypatch):
        knobs = {"dotmac_erp_push_redrive_stale_minutes": 240}
        monkeypatch.setattr(
            "app.services.settings_spec.resolve_value",
            lambda db, domain, key, **kw: knobs.get(key),
        )
        # 2h old: stale under the 30-minute default, fresh under a 4h threshold.
        _work_order(db_session, ERP_SYNC_PENDING, updated_at=STALE)
        send_task = MagicMock()

        result = redrive_failed_erp_pushes(db_session, send_task=send_task)

        assert result["stale_minutes"] == 240
        assert result["total"] == 0
        send_task.assert_not_called()
