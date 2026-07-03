"""Backfill of existing paid sales orders → sub (installation payment + subscriptions)."""

import uuid
from decimal import Decimal

from app.models.sales_order import SalesOrder, SalesOrderPaymentStatus
from app.services import billing_sync


def _order(db_session, person, payment_status, amount="5000") -> SalesOrder:
    so = SalesOrder(
        person_id=person.id,
        order_number=f"SO-{uuid.uuid4().hex[:8]}",
        payment_status=payment_status,
        total=Decimal(amount),
        amount_paid=Decimal(amount) if payment_status != SalesOrderPaymentStatus.pending else Decimal("0"),
    )
    db_session.add(so)
    db_session.commit()
    db_session.refresh(so)
    return so


def test_backfill_pushes_only_settled_orders(db_session, person, monkeypatch):
    pushed = []
    import app.services.events.handlers.selfcare_customer as handler

    monkeypatch.setattr(handler, "push_sales_order_payment_to_selfcare", lambda db, so: pushed.append(so.id))

    paid = _order(db_session, person, SalesOrderPaymentStatus.paid)
    partial = _order(db_session, person, SalesOrderPaymentStatus.partial)
    _order(db_session, person, SalesOrderPaymentStatus.pending)  # excluded

    result = billing_sync.backfill_sales_payments_to_sub(db_session, limit=100)
    assert result["processed"] == 2
    assert set(pushed) == {paid.id, partial.id}


def test_backfill_pushes_both_payment_and_subscription(db_session, person, monkeypatch):
    import app.services.events.handlers.selfcare_customer as handler

    pay, subs = [], []
    monkeypatch.setattr(handler, "push_sales_order_payment_to_selfcare", lambda db, so: pay.append(so.id))
    monkeypatch.setattr(handler, "push_sales_order_subscription_to_selfcare", lambda db, so: subs.append(so.id))

    paid = _order(db_session, person, SalesOrderPaymentStatus.paid)
    result = billing_sync.backfill_sales_payments_to_sub(db_session, limit=100)
    assert result["processed"] == 1
    assert pay == [paid.id] and subs == [paid.id]


def test_backfill_is_resilient_to_a_bad_row(db_session, person, monkeypatch):
    import app.services.events.handlers.selfcare_customer as handler

    def _boom(db, so):
        raise RuntimeError("selfcare down")

    # Both pushes failing is logged, not raised — the sweep continues.
    monkeypatch.setattr(handler, "push_sales_order_payment_to_selfcare", _boom)
    monkeypatch.setattr(handler, "push_sales_order_subscription_to_selfcare", _boom)
    _order(db_session, person, SalesOrderPaymentStatus.paid)
    result = billing_sync.backfill_sales_payments_to_sub(db_session, limit=100)
    assert result["processed"] == 1
