"""Tests for reseller commissions + payouts."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.person import Person
from app.models.reseller_commission import CommissionStatus, PayoutStatus, ResellerCommission
from app.models.sales_order import SalesOrder, SalesOrderPaymentStatus
from app.models.subscriber import AccountType, Organization
from app.services import reseller_commissions as mod
from app.services.reseller_commissions import reseller_commissions as svc


def _enable(monkeypatch, enabled: bool = True, default_rate: str = "0"):
    def _resolve(db, domain, key, use_cache=True):
        if key == "reseller_commissions_enabled":
            return enabled
        if key == "reseller_commission_default_rate":
            return default_rate
        return None

    monkeypatch.setattr(mod.settings_spec, "resolve_value", _resolve)


def _org(db, name, account_type, parent_id=None, rate=None):
    org = Organization(name=name, account_type=account_type, parent_id=parent_id, commission_rate=rate)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _person(db, org_id=None):
    p = Person(first_name="C", last_name="R", email=f"p-{uuid.uuid4().hex[:8]}@example.com", organization_id=org_id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _paid_order(db, person, total="100000"):
    so = SalesOrder(
        person_id=person.id,
        total=Decimal(total),
        currency="NGN",
        payment_status=SalesOrderPaymentStatus.paid,
    )
    db.add(so)
    db.commit()
    db.refresh(so)
    return so


def _reseller_customer_order(db, *, rate=None, total="100000"):
    reseller = _org(db, "Reseller X", AccountType.reseller, rate=rate)
    customer = _org(db, "Customer Y", AccountType.customer, parent_id=reseller.id)
    person = _person(db, org_id=customer.id)
    order = _paid_order(db, person, total=total)
    return reseller, person, order


def test_accrue_uses_per_reseller_rate(db_session, monkeypatch):
    _enable(monkeypatch)
    reseller, _person_, order = _reseller_customer_order(db_session, rate=Decimal("10"))
    commission = svc.accrue_for_sales_order(db_session, order)
    assert commission is not None
    assert commission.reseller_org_id == reseller.id
    assert commission.rate == Decimal("10")
    assert commission.amount == Decimal("10000.00")
    assert commission.status == CommissionStatus.pending


def test_accrue_falls_back_to_default_rate(db_session, monkeypatch):
    _enable(monkeypatch, default_rate="5")
    _reseller, _person_, order = _reseller_customer_order(db_session, rate=None, total="200000")
    commission = svc.accrue_for_sales_order(db_session, order)
    assert commission.rate == Decimal("5")
    assert commission.amount == Decimal("10000.00")


def test_accrue_is_idempotent(db_session, monkeypatch):
    _enable(monkeypatch)
    _reseller, _person_, order = _reseller_customer_order(db_session, rate=Decimal("10"))
    first = svc.accrue_for_sales_order(db_session, order)
    second = svc.accrue_for_sales_order(db_session, order)
    assert first.id == second.id
    assert db_session.query(ResellerCommission).filter(ResellerCommission.sales_order_id == order.id).count() == 1


def test_no_commission_when_not_reseller_sourced(db_session, monkeypatch):
    _enable(monkeypatch, default_rate="10")
    org = _org(db_session, "Direct Co", AccountType.customer)  # no reseller ancestor
    person = _person(db_session, org_id=org.id)
    order = _paid_order(db_session, person)
    assert svc.accrue_for_sales_order(db_session, order) is None


def test_disabled_accrues_nothing(db_session, monkeypatch):
    _enable(monkeypatch, enabled=False, default_rate="10")
    _reseller, _person_, order = _reseller_customer_order(db_session, rate=Decimal("10"))
    assert svc.accrue_for_sales_order(db_session, order) is None
    assert db_session.query(ResellerCommission).count() == 0


def test_unpaid_order_accrues_nothing(db_session, monkeypatch):
    _enable(monkeypatch)
    reseller = _org(db_session, "Reseller", AccountType.reseller, rate=Decimal("10"))
    customer = _org(db_session, "Cust", AccountType.customer, parent_id=reseller.id)
    person = _person(db_session, org_id=customer.id)
    order = SalesOrder(person_id=person.id, total=Decimal("100000"), payment_status=SalesOrderPaymentStatus.pending)
    db_session.add(order)
    db_session.commit()
    assert svc.accrue_for_sales_order(db_session, order) is None


def test_approve_payout_and_mark_paid(db_session, monkeypatch):
    _enable(monkeypatch)
    reseller, _person_, order = _reseller_customer_order(db_session, rate=Decimal("10"))
    commission = svc.accrue_for_sales_order(db_session, order)

    svc.approve(db_session, str(commission.id))
    db_session.refresh(commission)
    assert commission.status == CommissionStatus.approved

    payout = svc.create_payout(db_session, str(reseller.id))
    assert payout.status == PayoutStatus.draft
    assert payout.total_amount == Decimal("10000.00")
    db_session.refresh(commission)
    assert commission.payout_id == payout.id

    svc.mark_payout_paid(db_session, str(payout.id), method="bank", reference="TXN-1")
    db_session.refresh(payout)
    db_session.refresh(commission)
    assert payout.status == PayoutStatus.paid
    assert payout.paid_at is not None
    assert commission.status == CommissionStatus.paid


def test_create_payout_without_approved_is_409(db_session, monkeypatch):
    _enable(monkeypatch)
    reseller, _person_, order = _reseller_customer_order(db_session, rate=Decimal("10"))
    svc.accrue_for_sales_order(db_session, order)  # pending, not approved
    with pytest.raises(HTTPException) as exc:
        svc.create_payout(db_session, str(reseller.id))
    assert exc.value.status_code == 409


def test_accrue_on_paid_transition_via_update(db_session, monkeypatch):
    """Commissions must accrue when an order transitions to paid through update,
    not only at create time."""
    _enable(monkeypatch)
    from app.services.sales_orders import sales_orders

    reseller = _org(db_session, "Reseller P", AccountType.reseller, rate=Decimal("10"))
    customer = _org(db_session, "Customer P", AccountType.customer, parent_id=reseller.id)
    person = _person(db_session, org_id=customer.id)
    # Pay-later: order starts unpaid, no commission yet.
    order = SalesOrder(
        person_id=person.id,
        total=Decimal("100000"),
        amount_paid=Decimal("0"),
        balance_due=Decimal("100000"),
        currency="NGN",
        payment_status=SalesOrderPaymentStatus.pending,
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    assert db_session.query(ResellerCommission).filter(ResellerCommission.sales_order_id == order.id).count() == 0

    # Mark paid via update_from_input (the web pay-later path) -> accrue.
    sales_orders.update_from_input(db_session, str(order.id), payment_status="paid")
    commissions = db_session.query(ResellerCommission).filter(ResellerCommission.sales_order_id == order.id).all()
    assert len(commissions) == 1
    assert commissions[0].amount == Decimal("10000.00")
    assert commissions[0].status == CommissionStatus.pending

    # Idempotent: a second paid-update must not create a duplicate.
    sales_orders.update_from_input(db_session, str(order.id), payment_status="paid")
    assert db_session.query(ResellerCommission).filter(ResellerCommission.sales_order_id == order.id).count() == 1


def test_create_payout_does_not_double_claim_commissions(db_session, monkeypatch):
    """Two payouts for the same reseller must not both claim the same commission."""
    _enable(monkeypatch)
    reseller, _person_, order = _reseller_customer_order(db_session, rate=Decimal("10"))
    commission = svc.accrue_for_sales_order(db_session, order)
    svc.approve(db_session, str(commission.id))

    first = svc.create_payout(db_session, str(reseller.id))
    db_session.refresh(commission)
    assert commission.payout_id == first.id

    # No remaining approved/unpaid commissions -> second payout is a 409.
    with pytest.raises(HTTPException) as exc:
        svc.create_payout(db_session, str(reseller.id))
    assert exc.value.status_code == 409
    # Commission stays attached to the first payout only.
    assert db_session.query(ResellerCommission).filter(ResellerCommission.payout_id == first.id).count() == 1


def test_void_attached_commission_refused(db_session, monkeypatch):
    """An approved commission already in a draft payout cannot be voided in place."""
    _enable(monkeypatch)
    reseller, _person_, order = _reseller_customer_order(db_session, rate=Decimal("10"))
    commission = svc.accrue_for_sales_order(db_session, order)
    svc.approve(db_session, str(commission.id))
    svc.create_payout(db_session, str(reseller.id))

    with pytest.raises(HTTPException) as exc:
        svc.void(db_session, str(commission.id))
    assert exc.value.status_code == 409
    db_session.refresh(commission)
    assert commission.status == CommissionStatus.approved


def test_void_then_mark_paid_skips_voided_commission(db_session, monkeypatch):
    """A voided commission must never be flipped to paid by mark_payout_paid."""
    _enable(monkeypatch)
    reseller = _org(db_session, "Reseller V", AccountType.reseller, rate=Decimal("10"))
    customer = _org(db_session, "Customer V", AccountType.customer, parent_id=reseller.id)
    p1 = _person(db_session, org_id=customer.id)
    p2 = _person(db_session, org_id=customer.id)
    order1 = _paid_order(db_session, p1, total="100000")
    order2 = _paid_order(db_session, p2, total="50000")
    c1 = svc.accrue_for_sales_order(db_session, order1)
    c2 = svc.accrue_for_sales_order(db_session, order2)
    svc.approve(db_session, str(c1.id))
    svc.approve(db_session, str(c2.id))

    # Void c2 BEFORE it is attached to a payout.
    svc.void(db_session, str(c2.id))
    db_session.refresh(c2)
    assert c2.status == CommissionStatus.void

    # Payout now only picks up the approved c1.
    payout = svc.create_payout(db_session, str(reseller.id))
    assert payout.total_amount == Decimal("10000.00")

    svc.mark_payout_paid(db_session, str(payout.id), method="bank", reference="TXN-9")
    db_session.refresh(c1)
    db_session.refresh(c2)
    assert c1.status == CommissionStatus.paid
    assert c2.status == CommissionStatus.void  # NOT resurrected to paid


def test_create_payout_rejects_mixed_currencies(db_session, monkeypatch):
    """Approved commissions in different currencies cannot be paid out together."""
    _enable(monkeypatch)
    reseller = _org(db_session, "Reseller C", AccountType.reseller, rate=Decimal("10"))
    customer = _org(db_session, "Customer C", AccountType.customer, parent_id=reseller.id)
    p1 = _person(db_session, org_id=customer.id)
    p2 = _person(db_session, org_id=customer.id)
    order1 = _paid_order(db_session, p1, total="100000")
    order2 = _paid_order(db_session, p2, total="100000")
    order2.currency = "USD"
    db_session.commit()
    c1 = svc.accrue_for_sales_order(db_session, order1)
    c2 = svc.accrue_for_sales_order(db_session, order2)
    assert c1.currency == "NGN"
    assert c2.currency == "USD"
    svc.approve(db_session, str(c1.id))
    svc.approve(db_session, str(c2.id))

    with pytest.raises(HTTPException) as exc:
        svc.create_payout(db_session, str(reseller.id))
    assert exc.value.status_code == 409


def test_mark_payout_paid_preserves_existing_method_on_remark(db_session, monkeypatch):
    """A second mark-paid with no args must not clobber recorded method/reference."""
    _enable(monkeypatch)
    reseller, _person_, order = _reseller_customer_order(db_session, rate=Decimal("10"))
    commission = svc.accrue_for_sales_order(db_session, order)
    svc.approve(db_session, str(commission.id))
    payout = svc.create_payout(db_session, str(reseller.id))
    svc.mark_payout_paid(db_session, str(payout.id), method="bank", reference="TXN-1")
    # Already paid -> early return, details preserved.
    svc.mark_payout_paid(db_session, str(payout.id), method=None, reference=None)
    db_session.refresh(payout)
    assert payout.method == "bank"
    assert payout.reference == "TXN-1"


def test_summary_aggregates_by_status(db_session, monkeypatch):
    _enable(monkeypatch)
    reseller, _person_, order = _reseller_customer_order(db_session, rate=Decimal("10"))
    commission = svc.accrue_for_sales_order(db_session, order)
    summary = svc.reseller_summary(db_session, str(reseller.id))
    assert summary["total_commissions"] == 1
    assert summary["pending_amount"] == Decimal("10000.00")
    assert summary["unpaid_amount"] == Decimal("10000.00")
    svc.approve(db_session, str(commission.id))
    summary = svc.reseller_summary(db_session, str(reseller.id))
    assert summary["approved_amount"] == Decimal("10000.00")
