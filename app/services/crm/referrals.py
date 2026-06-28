"""Referral program service.

Closed loop: an active subscriber gets a code → a prospect captures via that code
(creating an attributed lead) → the referral qualifies when the prospect becomes
an active subscriber → the referrer earns a configurable account credit.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crm.enums import LeadStatus
from app.models.crm.referral import (
    Referral,
    ReferralCode,
    ReferralRewardStatus,
    ReferralStatus,
)
from app.models.crm.sales import Lead
from app.models.domain_settings import SettingDomain
from app.models.person import ChannelType, Person
from app.models.subscriber import Subscriber, SubscriberStatus
from app.services import settings_spec
from app.services.common import coerce_uuid, get_or_404, validate_enum
from app.services.person_identity import resolve_person

logger = logging.getLogger(__name__)

# Unambiguous alphabet (no 0/O/1/I) so codes are easy to share verbally.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
_REFERRAL_LEAD_SOURCE = "Referral"
_DOMAIN = SettingDomain.subscriber


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _config(db: Session) -> dict:
    amount_raw = settings_spec.resolve_value(db, _DOMAIN, "referral_reward_amount", use_cache=False)
    try:
        amount = Decimal(str(amount_raw)) if amount_raw not in (None, "") else Decimal("0")
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    window_raw = settings_spec.resolve_value(db, _DOMAIN, "referral_qualify_window_days", use_cache=False)
    try:
        window = int(cast(str | int, window_raw)) if window_raw not in (None, "") else 90
    except (TypeError, ValueError):
        window = 90
    return {
        "enabled": _as_bool(
            settings_spec.resolve_value(db, _DOMAIN, "referral_program_enabled", use_cache=False), False
        ),
        "amount": amount,
        "currency": str(settings_spec.resolve_value(db, _DOMAIN, "referral_reward_currency", use_cache=False) or "NGN"),
        "window_days": window,
        "auto_approve": _as_bool(
            settings_spec.resolve_value(db, _DOMAIN, "referral_auto_approve_reward", use_cache=False), False
        ),
    }


def _generate_code(db: Session) -> str:
    for _ in range(12):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        if not db.query(ReferralCode).filter(ReferralCode.code == code).first():
            return code
    raise HTTPException(status_code=500, detail="Could not generate a unique referral code")


def _referrer_subscriber_id(db: Session, referrer_person_id) -> str | None:
    """Resolve the referrer's dotmac_sub subscriber id (the credit target)."""
    sub = (
        db.query(Subscriber)
        .filter(Subscriber.person_id == referrer_person_id)
        .filter(Subscriber.is_active.is_(True))
        .filter(Subscriber.external_system == "selfcare")
        .order_by(Subscriber.updated_at.desc())
        .first()
    )
    if sub is not None and sub.external_id:
        return str(sub.external_id)
    person = db.get(Person, referrer_person_id)
    metadata = person.metadata_ if person is not None and isinstance(person.metadata_, dict) else {}
    selfcare_id = metadata.get("selfcare_id")
    if selfcare_id:
        return str(selfcare_id)
    return None


class Referrals:
    @staticmethod
    def ensure_code(db: Session, person_id: str) -> ReferralCode:
        """Get (or mint) the active referral code for a referrer."""
        pid = coerce_uuid(person_id)
        person = db.get(Person, pid)
        if person is None:
            raise HTTPException(status_code=404, detail="Person not found")
        existing = (
            db.query(ReferralCode)
            .filter(ReferralCode.person_id == pid)
            .filter(ReferralCode.is_active.is_(True))
            .first()
        )
        if existing:
            return existing
        code = ReferralCode(person_id=pid, code=_generate_code(db))
        db.add(code)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = (
                db.query(ReferralCode)
                .filter(ReferralCode.person_id == pid)
                .filter(ReferralCode.is_active.is_(True))
                .first()
            )
            if existing:
                return existing
            raise
        db.refresh(code)
        return code

    @staticmethod
    def get_by_code(db: Session, code: str) -> ReferralCode | None:
        normalized = str(code or "").strip().upper()
        if not normalized:
            return None
        return (
            db.query(ReferralCode)
            .filter(ReferralCode.code == normalized)
            .filter(ReferralCode.is_active.is_(True))
            .first()
        )

    @staticmethod
    def capture(
        db: Session,
        *,
        code: str,
        name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        region: str | None = None,
        address: str | None = None,
        notes: str | None = None,
        source: str = "public",
    ) -> Referral:
        """Record a referred prospect: resolve/create their Person, an attributed
        lead, and a pending Referral. Idempotent per referred person."""
        cfg = _config(db)
        if not cfg["enabled"]:
            raise HTTPException(status_code=503, detail="Referral program is not enabled.")

        ref_code = Referrals.get_by_code(db, code)
        if ref_code is None:
            raise HTTPException(status_code=404, detail="Invalid referral code.")

        email = (email or "").strip() or None
        phone = (phone or "").strip() or None
        if not email and not phone:
            raise HTTPException(status_code=422, detail="An email or phone number is required to refer someone.")

        channel_type = ChannelType.email if email else ChannelType.phone
        address_value = email if email else phone
        if address_value is None:
            raise HTTPException(status_code=422, detail="An email or phone number is required to refer someone.")
        resolved = resolve_person(
            db,
            channel_type=channel_type,
            address=address_value,
            display_name=name,
            email=email,
            phone=phone,
        )
        referred = resolved.person

        if referred.id == ref_code.person_id:
            raise HTTPException(status_code=409, detail="You can't refer yourself.")

        already_subscriber = (
            db.query(Subscriber)
            .filter(Subscriber.person_id == referred.id)
            .filter(Subscriber.status == SubscriberStatus.active)
            .filter(Subscriber.is_active.is_(True))
            .first()
        )
        if already_subscriber is not None:
            raise HTTPException(status_code=409, detail="That person is already an active customer.")

        existing = (
            db.query(Referral)
            .filter(Referral.referred_person_id == referred.id)
            .filter(Referral.is_active.is_(True))
            .first()
        )
        if existing is not None:
            return existing

        lead = Lead(
            person_id=referred.id,
            title=f"Referral: {referred.display_name or email or phone}",
            status=LeadStatus.new,
            lead_source=_REFERRAL_LEAD_SOURCE,
            region=region,
            address=address,
            notes=notes,
            metadata_={
                "referral_code": ref_code.code,
                "referrer_person_id": str(ref_code.person_id),
            },
        )
        db.add(lead)
        db.flush()

        referral = Referral(
            referrer_person_id=ref_code.person_id,
            referral_code_id=ref_code.id,
            referred_person_id=referred.id,
            referred_lead_id=lead.id,
            status=ReferralStatus.pending,
            reward_currency=cfg["currency"],
            source=source,
            metadata_={"capture": {"name": name, "email": email, "phone": phone}},
        )
        db.add(referral)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = (
                db.query(Referral)
                .filter(Referral.referred_person_id == referred.id)
                .filter(Referral.is_active.is_(True))
                .first()
            )
            if existing is not None:
                return existing
            raise
        db.refresh(referral)
        logger.info(
            "referral_captured referral_id=%s referrer=%s referred=%s code=%s",
            referral.id,
            ref_code.person_id,
            referred.id,
            ref_code.code,
        )
        return referral

    @staticmethod
    def qualify_for_subscriber(db: Session, subscriber: Subscriber | None) -> Referral | None:
        """Qualify a pending referral when its referred prospect becomes an active
        subscriber. Idempotent and side-effect-safe to call on every sub sync."""
        if subscriber is None or subscriber.person_id is None:
            return None
        if subscriber.status != SubscriberStatus.active:
            return None
        cfg = _config(db)
        if not cfg["enabled"]:
            return None

        referral = (
            db.query(Referral)
            .filter(Referral.referred_person_id == subscriber.person_id)
            .filter(Referral.status == ReferralStatus.pending)
            .filter(Referral.is_active.is_(True))
            .first()
        )
        if referral is None:
            return None

        now = datetime.now(UTC)
        created = referral.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if cfg["window_days"] and created is not None and (now - created) > timedelta(days=cfg["window_days"]):
            referral.status = ReferralStatus.expired
            db.commit()
            db.refresh(referral)
            logger.info("referral_expired referral_id=%s", referral.id)
            return referral

        referral.status = ReferralStatus.qualified
        referral.referred_subscriber_id = subscriber.id
        referral.qualified_at = now
        referral.reward_amount = cfg["amount"]
        referral.reward_currency = cfg["currency"]
        referral.reward_status = ReferralRewardStatus.approved if cfg["auto_approve"] else ReferralRewardStatus.pending
        db.commit()
        db.refresh(referral)
        logger.info(
            "referral_qualified referral_id=%s referrer=%s amount=%s",
            referral.id,
            referral.referrer_person_id,
            referral.reward_amount,
        )
        return referral

    @staticmethod
    def issue_reward(db: Session, referral_id: str) -> Referral:
        """Apply the referrer's reward as an account credit in dotmac_sub and
        mark the referral rewarded. Raises 502 if the credit push fails (the
        referral stays qualified for retry)."""
        from app.services import selfcare

        # Lock the referral row so two concurrent calls can't both pass the status
        # check and double-credit (serializes the read-then-write).
        referral = db.query(Referral).filter(Referral.id == coerce_uuid(str(referral_id))).with_for_update().first()
        if referral is None:
            raise HTTPException(status_code=404, detail="Referral not found")
        if referral.status not in (ReferralStatus.qualified, ReferralStatus.rewarded):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot issue a reward for a referral in status {referral.status.value}",
            )

        # Already credited → idempotent: normalize status, never re-credit.
        if referral.reward_status == ReferralRewardStatus.issued:
            referral.status = ReferralStatus.rewarded
            db.commit()
            db.refresh(referral)
            return referral

        amount = referral.reward_amount or Decimal("0")
        if amount <= 0:
            # Never mark a referral "rewarded" with no credit behind it.
            raise HTTPException(status_code=400, detail="Referral has no positive reward amount to issue.")

        subscriber_id = _referrer_subscriber_id(db, referral.referrer_person_id)
        if not subscriber_id:
            raise HTTPException(
                status_code=409,
                detail="Referrer has no linked dotmac_sub subscriber account to credit.",
            )
        currency = (referral.reward_currency or "NGN").strip() or "NGN"
        try:
            credit_id = selfcare.create_account_credit(
                db,
                subscriber_id=subscriber_id,
                amount=amount,
                reason=f"Referral reward (referral {referral.id})",
                external_ref=f"referral:{referral.id}",
                currency=currency,
            )
        except selfcare.SelfcareProviderError as exc:
            logger.warning("referral_credit_push_failed referral_id=%s error=%s", referral.id, exc)
            raise HTTPException(
                status_code=502,
                detail="Could not apply the account credit in dotmac_sub. Try again.",
            ) from exc
        meta = dict(referral.metadata_ or {})
        meta["reward_credit_id"] = credit_id
        meta["reward_subscriber_id"] = subscriber_id
        referral.metadata_ = meta

        referral.reward_status = ReferralRewardStatus.issued
        referral.reward_issued_at = datetime.now(UTC)
        referral.status = ReferralStatus.rewarded
        db.commit()
        db.refresh(referral)
        logger.info(
            "referral_reward_issued referral_id=%s referrer=%s amount=%s credit=%s",
            referral.id,
            referral.referrer_person_id,
            referral.reward_amount,
            (referral.metadata_ or {}).get("reward_credit_id"),
        )
        return referral

    @staticmethod
    def reject(db: Session, referral_id: str, reason: str) -> Referral:
        referral = get_or_404(db, Referral, str(referral_id), "Referral not found")
        referral.status = ReferralStatus.rejected
        referral.reward_status = ReferralRewardStatus.void
        marker = f"Rejected: {reason}"
        referral.notes = f"{referral.notes}\n{marker}" if referral.notes else marker
        db.commit()
        db.refresh(referral)
        return referral

    @staticmethod
    def list(
        db: Session,
        *,
        status: str | None = None,
        referrer_person_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Referral]:
        query = db.query(Referral).filter(Referral.is_active.is_(True))
        if status:
            query = query.filter(Referral.status == validate_enum(status, ReferralStatus, "status"))
        if referrer_person_id:
            query = query.filter(Referral.referrer_person_id == coerce_uuid(referrer_person_id))
        return query.order_by(Referral.created_at.desc()).limit(limit).offset(offset).all()

    @staticmethod
    def get(db: Session, referral_id: str) -> Referral:
        return get_or_404(db, Referral, str(referral_id), "Referral not found")


referrals = Referrals()
