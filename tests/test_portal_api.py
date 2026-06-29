"""Portal API foundation: mint → scoped access rails (RFC #73)."""

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db


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
        json={"crm_subscriber_id": "sub-123", "actor": "subscriber", "scopes": ["read:referrals"]},
    )
    assert r.status_code == 200, r.text
    token = r.json()["portal_token"]
    assert r.json()["expires_at"] > 0

    me = client.get("/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["subject_id"] == "sub-123"
    assert body["actor"] == "subscriber"
    assert "read:referrals" in body["scopes"]


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
