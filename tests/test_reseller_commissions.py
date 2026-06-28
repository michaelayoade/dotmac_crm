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
