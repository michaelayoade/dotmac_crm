"""Tests for the referral program service."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.crm.referral import Referral, ReferralRewardStatus, ReferralStatus
from app.models.crm.sales import Lead
from app.models.person import Person
from app.models.subscriber import Subscriber, SubscriberStatus
from app.services.crm import referrals as referrals_module
from app.services.crm.referrals import referrals as svc


def _enable(monkeypatch, **overrides):
    values = {
        "referral_program_enabled": True,
        "referral_reward_amount": "5000",
        "referral_reward_currency": "NGN",
        "referral_qualify_window_days": 90,
        "referral_auto_approve_reward": False,
        **overrides,
    }

    def _resolve(_db, _domain, key, use_cache=True):
        return values.get(key)

    monkeypatch.setattr(referrals_module.settings_spec, "resolve_value", _resolve)


def _person(db, **kw):
    p = Person(
        first_name=kw.get("first_name", "Ref"),
        last_name=kw.get("last_name", "Errer"),
        email=kw.get("email", f"p-{uuid.uuid4().hex[:8]}@example.com"),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_ensure_code_is_idempotent(db_session, monkeypatch):
    _enable(monkeypatch)
    referrer = _person(db_session)
    code1 = svc.ensure_code(db_session, str(referrer.id))
    code2 = svc.ensure_code(db_session, str(referrer.id))
    assert code1.id == code2.id
    assert len(code1.code) == 8
    assert code1.code.isupper()


def test_capture_creates_lead_and_pending_referral(db_session, monkeypatch):
    _enable(monkeypatch)
    referrer = _person(db_session)
    code = svc.ensure_code(db_session, str(referrer.id))

    referral = svc.capture(
        db_session, code=code.code, name="New Prospect", email="prospect@example.com"
    )

    assert referral.status == ReferralStatus.pending
    assert referral.referrer_person_id == referrer.id
    assert referral.reward_status == ReferralRewardStatus.none
    lead = db_session.get(Lead, referral.referred_lead_id)
    assert lead is not None
    assert lead.lead_source == "Referral"
    assert lead.metadata_["referral_code"] == code.code


def test_capture_is_case_insensitive_and_dedups(db_session, monkeypatch):
    _enable(monkeypatch)
    referrer = _person(db_session)
    code = svc.ensure_code(db_session, str(referrer.id))

    first = svc.capture(db_session, code=code.code.lower(), email="dup@example.com")
    second = svc.capture(db_session, code=code.code, email="dup@example.com")
    assert first.id == second.id
    assert db_session.query(Referral).filter(Referral.referrer_person_id == referrer.id).count() == 1


def test_self_referral_is_rejected(db_session, monkeypatch):
    _enable(monkeypatch)
    referrer = _person(db_session, email="self@example.com")
    code = svc.ensure_code(db_session, str(referrer.id))
    with pytest.raises(HTTPException) as exc:
        svc.capture(db_session, code=code.code, email="self@example.com")
    assert exc.value.status_code == 409


def test_capture_requires_email_or_phone(db_session, monkeypatch):
    _enable(monkeypatch)
    referrer = _person(db_session)
    code = svc.ensure_code(db_session, str(referrer.id))
    with pytest.raises(HTTPException) as exc:
        svc.capture(db_session, code=code.code, name="No Contact")
    assert exc.value.status_code == 422


def test_capture_blocked_when_disabled(db_session, monkeypatch):
    _enable(monkeypatch, referral_program_enabled=False)
    referrer = _person(db_session)
    code = svc.ensure_code(db_session, str(referrer.id))
    with pytest.raises(HTTPException) as exc:
        svc.capture(db_session, code=code.code, email="x@example.com")
    assert exc.value.status_code == 503


def test_qualify_on_active_subscriber_earns_reward(db_session, monkeypatch):
    _enable(monkeypatch)
    referrer = _person(db_session)
    code = svc.ensure_code(db_session, str(referrer.id))
    referral = svc.capture(db_session, code=code.code, email="becomes-customer@example.com")

    # The referred prospect becomes an active subscriber.
    sub = Subscriber(
        external_system="selfcare",
        external_id="sc-1",
        subscriber_number=f"SUB-{uuid.uuid4().hex[:8]}",
        status=SubscriberStatus.active,
        person_id=referral.referred_person_id,
    )
    db_session.add(sub)
    db_session.commit()

    result = svc.qualify_for_subscriber(db_session, sub)
    assert result is not None
    db_session.refresh(referral)
    assert referral.status == ReferralStatus.qualified
    assert referral.reward_amount == Decimal("5000")
    assert referral.reward_status == ReferralRewardStatus.pending  # not auto-approved
    assert referral.referred_subscriber_id == sub.id

    # Idempotent: re-running does not double-process (still qualified, not re-pending).
    again = svc.qualify_for_subscriber(db_session, sub)
    assert again is None  # no pending referral left to qualify


def test_auto_approve_sets_reward_approved(db_session, monkeypatch):
    _enable(monkeypatch, referral_auto_approve_reward=True)
    referrer = _person(db_session)
    code = svc.ensure_code(db_session, str(referrer.id))
    referral = svc.capture(db_session, code=code.code, email="auto@example.com")
    sub = Subscriber(
        external_system="selfcare",
        external_id="sc-2",
        subscriber_number=f"SUB-{uuid.uuid4().hex[:8]}",
        status=SubscriberStatus.active,
        person_id=referral.referred_person_id,
    )
    db_session.add(sub)
    db_session.commit()
    svc.qualify_for_subscriber(db_session, sub)
    db_session.refresh(referral)
    assert referral.reward_status == ReferralRewardStatus.approved


def test_pending_subscriber_does_not_qualify(db_session, monkeypatch):
    _enable(monkeypatch)
    referrer = _person(db_session)
    code = svc.ensure_code(db_session, str(referrer.id))
    referral = svc.capture(db_session, code=code.code, email="pending@example.com")
    sub = Subscriber(
        external_system="selfcare",
        external_id="sc-3",
        subscriber_number=f"SUB-{uuid.uuid4().hex[:8]}",
        status=SubscriberStatus.pending,
        person_id=referral.referred_person_id,
    )
    db_session.add(sub)
    db_session.commit()
    assert svc.qualify_for_subscriber(db_session, sub) is None
    db_session.refresh(referral)
    assert referral.status == ReferralStatus.pending


def _referrer_subscriber(db, person, external_id):
    sub = Subscriber(
        external_system="selfcare",
        external_id=external_id,
        subscriber_number=f"SUB-{uuid.uuid4().hex[:8]}",
        status=SubscriberStatus.active,
        person_id=person.id,
    )
    db.add(sub)
    db.commit()
    return sub


def _qualified_referral(db, monkeypatch, *, referrer, referred_email, referred_ext_id):
    code = svc.ensure_code(db, str(referrer.id))
    referral = svc.capture(db, code=code.code, email=referred_email)
    sub = Subscriber(
        external_system="selfcare",
        external_id=referred_ext_id,
        subscriber_number=f"SUB-{uuid.uuid4().hex[:8]}",
        status=SubscriberStatus.active,
        person_id=referral.referred_person_id,
    )
    db.add(sub)
    db.commit()
    svc.qualify_for_subscriber(db, sub)
    return referral


def test_issue_reward_pushes_credit_and_marks_issued(db_session, monkeypatch):
    import app.services.selfcare as selfcare_mod

    _enable(monkeypatch)
    referrer = _person(db_session)
    _referrer_subscriber(db_session, referrer, external_id="ref-sub-1")
    referral = _qualified_referral(
        db_session, monkeypatch, referrer=referrer, referred_email="reward@example.com", referred_ext_id="sc-4"
    )

    pushed = {}

    def _fake_credit(db, *, subscriber_id, amount, reason, external_ref, currency):
        pushed.update({"subscriber_id": subscriber_id, "amount": amount, "external_ref": external_ref})
        return "credit-xyz"

    monkeypatch.setattr(selfcare_mod, "create_account_credit", _fake_credit)

    issued = svc.issue_reward(db_session, str(referral.id))
    assert issued.status == ReferralStatus.rewarded
    assert issued.reward_status == ReferralRewardStatus.issued
    assert issued.reward_issued_at is not None
    assert issued.metadata_["reward_credit_id"] == "credit-xyz"
    assert pushed["subscriber_id"] == "ref-sub-1"
    assert pushed["external_ref"] == f"referral:{referral.id}"


def test_issue_reward_without_referrer_subscriber_is_409(db_session, monkeypatch):
    _enable(monkeypatch)
    referrer = _person(db_session)  # no subscriber account → can't be credited
    referral = _qualified_referral(
        db_session, monkeypatch, referrer=referrer, referred_email="nosub@example.com", referred_ext_id="sc-5"
    )
    with pytest.raises(HTTPException) as exc:
        svc.issue_reward(db_session, str(referral.id))
    assert exc.value.status_code == 409


def test_issue_reward_credit_failure_is_502_and_stays_qualified(db_session, monkeypatch):
    import app.services.selfcare as selfcare_mod

    _enable(monkeypatch)
    referrer = _person(db_session)
    _referrer_subscriber(db_session, referrer, external_id="ref-sub-2")
    referral = _qualified_referral(
        db_session, monkeypatch, referrer=referrer, referred_email="boom@example.com", referred_ext_id="sc-6"
    )

    def _boom(*a, **k):
        raise selfcare_mod.SelfcareProviderError("dotmac_sub down")

    monkeypatch.setattr(selfcare_mod, "create_account_credit", _boom)

    with pytest.raises(HTTPException) as exc:
        svc.issue_reward(db_session, str(referral.id))
    assert exc.value.status_code == 502
    db_session.refresh(referral)
    assert referral.reward_status != ReferralRewardStatus.issued  # left for retry
    assert referral.status == ReferralStatus.qualified


def test_reject_voids_reward(db_session, monkeypatch):
    _enable(monkeypatch)
    referrer = _person(db_session)
    code = svc.ensure_code(db_session, str(referrer.id))
    referral = svc.capture(db_session, code=code.code, email="reject@example.com")
    rejected = svc.reject(db_session, str(referral.id), "duplicate account")
    assert rejected.status == ReferralStatus.rejected
    assert rejected.reward_status == ReferralRewardStatus.void
    assert "duplicate account" in (rejected.notes or "")
