"""Portal API foundation: mint → scoped access rails (RFC #73)."""

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.models.person import Person
from app.models.subscriber import Subscriber, SubscriberStatus


def _client(db_session, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    from app.api.crm.portal import internal_router, require_portal_mint, router

    app = FastAPI()
    app.include_router(internal_router)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    # Bypass the service-account email gate; we're testing the mint→scope rails.
    app.dependency_overrides[require_portal_mint] = lambda: {"person_id": str(uuid4())}
    return TestClient(app)


def test_mint_then_me_roundtrip(db_session, monkeypatch):
    client = _client(db_session, monkeypatch)
    r = client.post(
        "/portal/internal/session",
        json={"crm_subscriber_id": "sub-123", "actor": "subscriber", "scopes": ["referrals:read"]},
    )
    assert r.status_code == 200, r.text
    token = r.json()["portal_token"]
    assert r.json()["expires_at"] > 0

    me = client.get("/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["subject_id"] == "sub-123"
    assert body["actor"] == "subscriber"
    assert "referrals:read" in body["scopes"]


def test_me_rejects_missing_and_bad_token(db_session, monkeypatch):
    client = _client(db_session, monkeypatch)
    assert client.get("/portal/me").status_code == 401
    assert client.get("/portal/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_mint_rejects_bad_actor(db_session, monkeypatch):
    client = _client(db_session, monkeypatch)
    r = client.post(
        "/portal/internal/session",
        json={"crm_subscriber_id": "sub-1", "actor": "intruder", "scopes": []},
    )
    assert r.status_code == 422


def test_access_token_is_not_accepted_as_portal_token(db_session, monkeypatch):
    # A token of the wrong typ must not authorize portal routes.
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    from app.services.auth_flow import _issue_access_token

    client = _client(db_session, monkeypatch)
    access = _issue_access_token(db_session, str(uuid4()), str(uuid4()))
    assert client.get("/portal/me", headers={"Authorization": f"Bearer {access}"}).status_code == 401


# --- Refer & Earn -----------------------------------------------------------


def _subscriber(db):
    p = Person(first_name="Cust", last_name="One", email=f"c-{uuid4().hex[:8]}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    sub = Subscriber(
        external_system="selfcare",
        external_id=f"sc-{uuid4().hex[:6]}",
        subscriber_number=f"SUB-{uuid4().hex[:8]}",
        status=SubscriberStatus.active,
        person_id=p.id,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _mint(client, subject_id, scopes, actor="subscriber"):
    r = client.post(
        "/portal/internal/session",
        json={"crm_subscriber_id": subject_id, "actor": actor, "scopes": scopes},
    )
    assert r.status_code == 200, r.text
    return r.json()["portal_token"]


def _enable_program(monkeypatch, enabled=True):
    from app.services.crm import referrals as referrals_module

    values = {
        "referral_program_enabled": enabled,
        "referral_reward_amount": "5000",
        "referral_reward_currency": "NGN",
        "referral_qualify_window_days": 90,
        "referral_auto_approve_reward": False,
    }
    monkeypatch.setattr(
        referrals_module.settings_spec,
        "resolve_value",
        lambda _db, _domain, key, use_cache=True: values.get(key),
    )


def test_referrals_list_returns_code_program_and_share_link(db_session, monkeypatch):
    _enable_program(monkeypatch)
    client = _client(db_session, monkeypatch)
    sub = _subscriber(db_session)
    token = _mint(client, str(sub.id), ["referrals:read"])

    r = client.get("/portal/referrals", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"]
    assert body["share_url"].endswith(f"/r/{body['code']}")
    assert body["program"]["enabled"] is True
    assert body["program"]["reward_amount"] == "5000"
    assert body["totals"]["total"] == 0
    assert body["referrals"] == []


def test_refer_a_friend_then_appears_in_list(db_session, monkeypatch):
    _enable_program(monkeypatch)
    client = _client(db_session, monkeypatch)
    sub = _subscriber(db_session)
    token = _mint(client, str(sub.id), ["referrals:read", "referrals:write"])
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/portal/referrals", json={"name": "Friend", "email": "friend@example.com"}, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"

    lst = client.get("/portal/referrals", headers=headers)
    assert lst.status_code == 200
    body = lst.json()
    assert body["totals"]["total"] == 1
    assert body["totals"]["pending"] == 1
    assert body["referrals"][0]["referred_name"] == "Friend"


def test_referrals_requires_read_scope(db_session, monkeypatch):
    _enable_program(monkeypatch)
    client = _client(db_session, monkeypatch)
    sub = _subscriber(db_session)
    token = _mint(client, str(sub.id), [])  # no scopes granted
    r = client.get("/portal/referrals", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_refer_requires_write_scope(db_session, monkeypatch):
    _enable_program(monkeypatch)
    client = _client(db_session, monkeypatch)
    sub = _subscriber(db_session)
    token = _mint(client, str(sub.id), ["referrals:read"])  # read only
    r = client.post(
        "/portal/referrals",
        json={"email": "x@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_referrals_rejected_for_reseller_actor(db_session, monkeypatch):
    _enable_program(monkeypatch)
    client = _client(db_session, monkeypatch)
    token = _mint(client, str(uuid4()), ["referrals:read"], actor="reseller")
    r = client.get("/portal/referrals", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
