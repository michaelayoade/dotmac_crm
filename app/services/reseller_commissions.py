"""Reseller commissions + payouts.

Accrues a commission when a reseller-sourced sales order is paid, attributing the
sale to the nearest ``account_type=reseller`` ancestor of the buyer's org.
Approved commissions are grouped into a payout and marked paid together.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.reseller_commission import (
    CommissionStatus,
    PayoutStatus,
    ResellerCommission,
    ResellerPayout,
)
from app.models.sales_order import SalesOrder, SalesOrderPaymentStatus
from app.models.subscriber import AccountType, Organization
from app.services import settings_spec
from app.services.common import coerce_uuid, get_or_404, validate_enum

logger = logging.getLogger(__name__)
_DOMAIN = SettingDomain.subscriber


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _config(db: Session) -> dict:
    rate_raw = settings_spec.resolve_value(db, _DOMAIN, "reseller_commission_default_rate", use_cache=False)
    try:
        default_rate = Decimal(str(rate_raw)) if rate_raw not in (None, "") else Decimal("0")
    except (InvalidOperation, TypeError, ValueError):
        default_rate = Decimal("0")
    return {
        "enabled": _as_bool(
            settings_spec.resolve_value(db, _DOMAIN, "reseller_commissions_enabled", use_cache=False), False
        ),
        "default_rate": default_rate,
    }


def resolve_reseller_org_id(db: Session, person: Person | None) -> uuid.UUID | None:
    """Walk the buyer org's parent chain to the nearest reseller org."""
    if person is None:
        return None
    org_id = getattr(person, "organization_id", None)
    seen: set = set()
    while org_id and org_id not in seen:
        seen.add(org_id)
        org = db.get(Organization, org_id)
        if org is None:
            return None
        if org.account_type == AccountType.reseller and org.is_active:
            return org.id
        org_id = org.parent_id
    return None


class ResellerCommissions:
    @staticmethod
    def accrue_for_sales_order(db: Session, sales_order: SalesOrder | None) -> ResellerCommission | None:
        """Accrue a pending commission for a paid, reseller-sourced order.
        Idempotent (one commission per order); a no-op when disabled, unpaid, or
        not attributable to a reseller."""
        cfg = _config(db)
        if not cfg["enabled"]:
            return None
        if sales_order is None or sales_order.payment_status != SalesOrderPaymentStatus.paid:
            return None

        existing = db.query(ResellerCommission).filter(ResellerCommission.sales_order_id == sales_order.id).first()
        if existing is not None:
            return existing

        person = db.get(Person, sales_order.person_id) if sales_order.person_id else None
        reseller_org_id = resolve_reseller_org_id(db, person)
        if not reseller_org_id:
            return None

        reseller = db.get(Organization, reseller_org_id)
        rate = reseller.commission_rate if reseller and reseller.commission_rate is not None else cfg["default_rate"]
        if rate is None or rate <= 0:
            return None
        basis = sales_order.total or Decimal("0")
        amount = (basis * rate / Decimal("100")).quantize(Decimal("0.01"))
        if amount <= 0:
            return None

        commission = ResellerCommission(
            reseller_org_id=reseller_org_id,
            sales_order_id=sales_order.id,
            person_id=person.id if person else None,
            basis_amount=basis,
            rate=rate,
            amount=amount,
            currency=sales_order.currency or "NGN",
            status=CommissionStatus.pending,
            earned_at=datetime.now(UTC),
        )
        db.add(commission)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.query(ResellerCommission).filter(ResellerCommission.sales_order_id == sales_order.id).first()
            if existing is not None:
                return existing
            raise
        db.refresh(commission)
        logger.info(
            "reseller_commission_accrued order=%s reseller=%s amount=%s",
            sales_order.id,
            reseller_org_id,
            amount,
        )
        return commission

    @staticmethod
    def approve(db: Session, commission_id: str) -> ResellerCommission:
        commission = get_or_404(db, ResellerCommission, str(commission_id), "Commission not found")
        if commission.status == CommissionStatus.void:
            raise HTTPException(status_code=409, detail="Cannot approve a voided commission.")
        if commission.status == CommissionStatus.pending:
            commission.status = CommissionStatus.approved
            db.commit()
            db.refresh(commission)
        return commission

    @staticmethod
    def void(db: Session, commission_id: str, reason: str | None = None) -> ResellerCommission:
        commission = get_or_404(db, ResellerCommission, str(commission_id), "Commission not found")
        if commission.status == CommissionStatus.paid:
            raise HTTPException(status_code=409, detail="Cannot void a paid commission.")
        if commission.payout_id is not None:
            # A commission already grouped into a (draft) payout must not be voided
            # in place: doing so would leave the payout total overstated and let
            # mark_payout_paid resurrect it. Detach it from the payout first.
            raise HTTPException(
                status_code=409,
                detail="Cannot void a commission attached to a payout; remove it from the payout first.",
            )
        commission.status = CommissionStatus.void
        if reason:
            marker = f"Voided: {reason}"
            commission.notes = f"{commission.notes}\n{marker}" if commission.notes else marker
        db.commit()
        db.refresh(commission)
        return commission

    @staticmethod
    def create_payout(db: Session, reseller_org_id: str) -> ResellerPayout:
        """Group the reseller's approved, unpaid commissions into a draft payout."""
        rid = coerce_uuid(reseller_org_id)
        # Lock the approved/unpaid rows so two concurrent payouts can't claim the
        # same commissions; skip_locked lets a parallel run grab a disjoint set.
        approved = (
            db.query(ResellerCommission)
            .filter(ResellerCommission.reseller_org_id == rid)
            .filter(ResellerCommission.status == CommissionStatus.approved)
            .filter(ResellerCommission.payout_id.is_(None))
            .filter(ResellerCommission.is_active.is_(True))
            .with_for_update(skip_locked=True)
            .all()
        )
        # Re-filter under the lock: a row claimed by a concurrent payout between
        # query planning and the locked read must not be double-assigned.
        approved = [c for c in approved if c.payout_id is None and c.status == CommissionStatus.approved]
        if not approved:
            raise HTTPException(status_code=409, detail="No approved commissions to pay out.")
        currencies = {(c.currency or "NGN") for c in approved}
        if len(currencies) > 1:
            raise HTTPException(
                status_code=409,
                detail="Approved commissions span multiple currencies; pay out one currency at a time.",
            )
        currency = approved[0].currency or "NGN"
        total = sum((c.amount for c in approved), Decimal("0"))
        payout = ResellerPayout(
            reseller_org_id=rid,
            total_amount=total,
            currency=currency,
            status=PayoutStatus.draft,
        )
        db.add(payout)
        db.flush()
        for c in approved:
            c.payout_id = payout.id
        db.commit()
        db.refresh(payout)
        logger.info("reseller_payout_created reseller=%s payout=%s total=%s", rid, payout.id, total)
        return payout

    @staticmethod
    def mark_payout_paid(
        db: Session, payout_id: str, *, method: str | None = None, reference: str | None = None
    ) -> ResellerPayout:
        pid = coerce_uuid(str(payout_id))
        # Re-read under a lock so concurrent mark-paid calls can't both flip it.
        payout = db.query(ResellerPayout).filter(ResellerPayout.id == pid).with_for_update().first()
        if payout is None:
            raise HTTPException(status_code=404, detail="Payout not found")
        if payout.status == PayoutStatus.paid:
            return payout
        if payout.status != PayoutStatus.draft:
            raise HTTPException(status_code=409, detail="Only draft payouts can be marked paid.")
        payout.status = PayoutStatus.paid
        payout.paid_at = datetime.now(UTC)
        # Only overwrite method/reference when supplied, so a re-mark or partial
        # input doesn't clobber previously recorded details.
        if method is not None:
            payout.method = method
        if reference is not None:
            payout.reference = reference
        # Only approved commissions become paid; voided/detached ones are skipped
        # so a voided commission can't be resurrected into a paid state.
        for c in payout.commissions:
            if c.status == CommissionStatus.approved:
                c.status = CommissionStatus.paid
        db.commit()
        db.refresh(payout)
        logger.info("reseller_payout_paid payout=%s total=%s", payout.id, payout.total_amount)
        return payout

    @staticmethod
    def reseller_summary(db: Session, reseller_org_id: str) -> dict:
        rid = coerce_uuid(reseller_org_id)
        rows = (
            db.query(ResellerCommission)
            .filter(ResellerCommission.reseller_org_id == rid)
            .filter(ResellerCommission.is_active.is_(True))
            .all()
        )

        def total(status: CommissionStatus) -> Decimal:
            return sum((c.amount for c in rows if c.status == status), Decimal("0"))

        return {
            "reseller_org_id": str(rid),
            "total_commissions": len(rows),
            "pending_amount": total(CommissionStatus.pending),
            "approved_amount": total(CommissionStatus.approved),
            "paid_amount": total(CommissionStatus.paid),
            "unpaid_amount": total(CommissionStatus.pending) + total(CommissionStatus.approved),
        }

    @staticmethod
    def list_commissions(
        db: Session,
        *,
        reseller_org_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ResellerCommission]:
        query = db.query(ResellerCommission).filter(ResellerCommission.is_active.is_(True))
        if reseller_org_id:
            query = query.filter(ResellerCommission.reseller_org_id == coerce_uuid(reseller_org_id))
        if status:
            query = query.filter(ResellerCommission.status == validate_enum(status, CommissionStatus, "status"))
        return query.order_by(ResellerCommission.created_at.desc()).limit(limit).offset(offset).all()

    @staticmethod
    def list_payouts(
        db: Session, *, reseller_org_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[ResellerPayout]:
        query = db.query(ResellerPayout).filter(ResellerPayout.is_active.is_(True))
        if reseller_org_id:
            query = query.filter(ResellerPayout.reseller_org_id == coerce_uuid(reseller_org_id))
        return query.order_by(ResellerPayout.created_at.desc()).limit(limit).offset(offset).all()


reseller_commissions = ResellerCommissions()
