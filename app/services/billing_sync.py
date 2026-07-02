"""Backfill existing CRM sales financials into dotmac_sub.

Re-runnable one-time migration: sweeps paid/partly-paid sales orders and pushes
each one's installation invoice + payment AND any subscription (plan) lines to
the subscriber app, so historical sales settle in the ledger and show in the
customer portal. Every push is idempotent server-side (invoices dedup on
external_ref, payments on ``crm:<ref>``, subscriptions on external_ref), so this
is safe to run repeatedly and to resume mid-way.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.sales_order import SalesOrder, SalesOrderPaymentStatus

logger = logging.getLogger(__name__)

_SETTLED_STATUSES = (SalesOrderPaymentStatus.paid, SalesOrderPaymentStatus.partial)


def backfill_sales_payments_to_sub(db: Session, *, limit: int = 500, offset: int = 0) -> dict:
    """Push one batch of paid/partial sales orders to sub — both the installation
    payment and any subscription (plan) lines. Returns {processed, batch_size} —
    processed is how many orders were swept (each push is a no-op if already
    synced or if selfcare is disabled)."""
    from app.services.events.handlers.selfcare_customer import (
        push_sales_order_payment_to_selfcare,
        push_sales_order_subscription_to_selfcare,
    )

    orders = (
        db.query(SalesOrder)
        .filter(SalesOrder.payment_status.in_(_SETTLED_STATUSES))
        .order_by(SalesOrder.created_at.asc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    processed = 0
    for order in orders:
        try:
            push_sales_order_payment_to_selfcare(db, order)
        except Exception:
            logger.warning("backfill_sales_payment_failed sales_order_id=%s", order.id, exc_info=True)
        try:
            push_sales_order_subscription_to_selfcare(db, order)
        except Exception:
            logger.warning("backfill_sales_subscription_failed sales_order_id=%s", order.id, exc_info=True)
        processed += 1
    return {"processed": processed, "batch_size": len(orders)}
