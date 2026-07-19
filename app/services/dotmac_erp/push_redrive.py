"""Re-drive ERP money pushes stuck in failed or stale in-flight sync states.

Audit item D1: a crm->erp money push (material request, purchase order,
purchase invoice) that fails after the CRM-side write must never be
terminal-invisible. Each push flow persists an ``erp_sync_status`` marker on
the row that owns the push intent; this sweep finds rows whose marker says
``failed`` -- or that have sat in ``pending``/``retrying`` longer than the
configured staleness threshold -- and re-enqueues the per-row sync task.

Re-driving is safe because every push uses a deterministic idempotency key
(``po-wo-{work_order_id}``, ``pinv-{invoice_id}``, and the material-request
key) and ERP keeps idempotency records, so a duplicate push is a no-op on the
ERP side.

Expense flows are intentionally excluded -- they have their own status-poll
machinery.

Config knobs (integration domain, see settings_spec):
- ``dotmac_erp_push_redrive_stale_minutes`` (default 30): a push left in
  pending/retrying longer than this is considered lost and re-enqueued.
- ``dotmac_erp_push_redrive_batch_limit`` (default 100): max rows re-driven
  per sweep run, across all three flows.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.material_request import MaterialRequest, MaterialRequestERPSyncStatus
from app.models.vendor import VendorPurchaseInvoice
from app.models.workforce import WorkOrder
from app.services import settings_spec

logger = logging.getLogger(__name__)

# Shared vocabulary for the string-backed erp_sync_status columns
# (work_orders, vendor_purchase_invoices). Mirrors MaterialRequestERPSyncStatus.
ERP_SYNC_PENDING = "pending"
ERP_SYNC_SYNCED = "synced"
ERP_SYNC_FAILED = "failed"
ERP_SYNC_RETRYING = "retrying"
ERP_SYNC_NOT_CONFIGURED = "not_configured"

MATERIAL_REQUEST_SYNC_TASK = "app.tasks.integrations.sync_material_request_to_erp"
PURCHASE_ORDER_SYNC_TASK = "app.tasks.integrations.sync_purchase_order_to_erp"
PURCHASE_INVOICE_SYNC_TASK = "app.tasks.integrations.sync_purchase_invoice_to_erp"

DEFAULT_STALE_MINUTES = 30
DEFAULT_BATCH_LIMIT = 100

SendTask = Callable[..., Any]


def _knob_int(session: Session, key: str, default: int) -> int:
    """Read an integer knob from integration settings, falling back to default."""
    value = settings_spec.resolve_value(session, SettingDomain.integration, key, use_cache=False)
    try:
        parsed = int(str(value)) if value is not None else default
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _redrive_material_requests(session: Session, cutoff: datetime, budget: int, send_task: SendTask) -> tuple[int, int]:
    if budget <= 0:
        return 0, 0
    rows = (
        session.query(MaterialRequest)
        .filter(MaterialRequest.is_active.is_(True))
        .filter(
            or_(
                MaterialRequest.erp_sync_status == MaterialRequestERPSyncStatus.failed,
                and_(
                    MaterialRequest.erp_sync_status.in_(
                        [MaterialRequestERPSyncStatus.pending, MaterialRequestERPSyncStatus.retrying]
                    ),
                    MaterialRequest.updated_at < cutoff,
                ),
            )
        )
        .order_by(MaterialRequest.updated_at.asc())
        .limit(budget)
        .all()
    )
    enqueued = 0
    errors = 0
    for mr in rows:
        mr.erp_sync_status = MaterialRequestERPSyncStatus.pending
        session.commit()
        try:
            send_task(MATERIAL_REQUEST_SYNC_TASK, args=[str(mr.id)])
            enqueued += 1
        except Exception as exc:
            mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
            mr.erp_sync_error = f"Re-drive enqueue failed: {exc}"[:500]
            session.commit()
            errors += 1
            logger.warning("ERP_PUSH_REDRIVE_ENQUEUE_FAILED kind=material_request id=%s", mr.id, exc_info=True)
    return enqueued, errors


def _redrive_purchase_orders(session: Session, cutoff: datetime, budget: int, send_task: SendTask) -> tuple[int, int]:
    if budget <= 0:
        return 0, 0
    rows = (
        session.query(WorkOrder)
        .filter(WorkOrder.is_active.is_(True))
        # Rows without a recorded quote id cannot be re-driven (the sync task
        # needs both args); they predate the status columns and are re-marked
        # the next time a quote approval enqueues them.
        .filter(WorkOrder.erp_po_quote_id.isnot(None))
        .filter(
            or_(
                WorkOrder.erp_sync_status == ERP_SYNC_FAILED,
                and_(
                    WorkOrder.erp_sync_status.in_([ERP_SYNC_PENDING, ERP_SYNC_RETRYING]),
                    WorkOrder.updated_at < cutoff,
                ),
            )
        )
        .order_by(WorkOrder.updated_at.asc())
        .limit(budget)
        .all()
    )
    enqueued = 0
    errors = 0
    for wo in rows:
        wo.erp_sync_status = ERP_SYNC_PENDING
        session.commit()
        try:
            send_task(PURCHASE_ORDER_SYNC_TASK, args=[str(wo.id), str(wo.erp_po_quote_id)])
            enqueued += 1
        except Exception as exc:
            wo.erp_sync_status = ERP_SYNC_FAILED
            wo.erp_sync_error = f"Re-drive enqueue failed: {exc}"[:500]
            session.commit()
            errors += 1
            logger.warning("ERP_PUSH_REDRIVE_ENQUEUE_FAILED kind=purchase_order id=%s", wo.id, exc_info=True)
    return enqueued, errors


def _redrive_purchase_invoices(session: Session, cutoff: datetime, budget: int, send_task: SendTask) -> tuple[int, int]:
    if budget <= 0:
        return 0, 0
    rows = (
        session.query(VendorPurchaseInvoice)
        .filter(VendorPurchaseInvoice.is_active.is_(True))
        .filter(
            or_(
                VendorPurchaseInvoice.erp_sync_status == ERP_SYNC_FAILED,
                and_(
                    VendorPurchaseInvoice.erp_sync_status.in_([ERP_SYNC_PENDING, ERP_SYNC_RETRYING]),
                    VendorPurchaseInvoice.updated_at < cutoff,
                ),
            )
        )
        .order_by(VendorPurchaseInvoice.updated_at.asc())
        .limit(budget)
        .all()
    )
    enqueued = 0
    errors = 0
    for invoice in rows:
        invoice.erp_sync_status = ERP_SYNC_PENDING
        session.commit()
        try:
            send_task(PURCHASE_INVOICE_SYNC_TASK, args=[str(invoice.id)])
            enqueued += 1
        except Exception as exc:
            invoice.erp_sync_status = ERP_SYNC_FAILED
            invoice.erp_sync_error = f"Re-drive enqueue failed: {exc}"[:500]
            session.commit()
            errors += 1
            logger.warning("ERP_PUSH_REDRIVE_ENQUEUE_FAILED kind=purchase_invoice id=%s", invoice.id, exc_info=True)
    return enqueued, errors


def redrive_failed_erp_pushes(session: Session, *, send_task: SendTask | None = None) -> dict[str, Any]:
    """Find failed/stale ERP money pushes and re-enqueue their sync tasks.

    Returns a dict of per-flow enqueue counts. ``send_task`` is injectable for
    tests; it defaults to ``celery_app.send_task`` (enqueue by task name).
    """
    if send_task is None:
        from app.celery_app import celery_app

        send_task = celery_app.send_task

    stale_minutes = _knob_int(session, "dotmac_erp_push_redrive_stale_minutes", DEFAULT_STALE_MINUTES)
    limit = _knob_int(session, "dotmac_erp_push_redrive_batch_limit", DEFAULT_BATCH_LIMIT)
    cutoff = datetime.now(UTC) - timedelta(minutes=stale_minutes)

    budget = limit
    mr_enqueued, mr_errors = _redrive_material_requests(session, cutoff, budget, send_task)
    budget -= mr_enqueued + mr_errors
    po_enqueued, po_errors = _redrive_purchase_orders(session, cutoff, budget, send_task)
    budget -= po_enqueued + po_errors
    pinv_enqueued, pinv_errors = _redrive_purchase_invoices(session, cutoff, budget, send_task)

    total = mr_enqueued + po_enqueued + pinv_enqueued
    enqueue_errors = mr_errors + po_errors + pinv_errors
    logger.info(
        "ERP_PUSH_REDRIVE_COMPLETE material_requests=%d purchase_orders=%d purchase_invoices=%d "
        "enqueue_errors=%d stale_minutes=%d limit=%d",
        mr_enqueued,
        po_enqueued,
        pinv_enqueued,
        enqueue_errors,
        stale_minutes,
        limit,
    )
    return {
        "material_requests": mr_enqueued,
        "purchase_orders": po_enqueued,
        "purchase_invoices": pinv_enqueued,
        "enqueue_errors": enqueue_errors,
        "total": total,
        "stale_minutes": stale_minutes,
        "limit": limit,
    }
