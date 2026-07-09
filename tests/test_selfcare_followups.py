"""Tests for the selfcare integration follow-ups: HMAC webhook, RBAC gating,
off-request provisioning, and the billing-risk enrichment cap."""

import hashlib
import hmac
import json
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.models.person import Gender, Person

# ── HMAC-signed inbound subscriber-sync webhook (#29) ─────────────────────────


def _webhook_client(db_session):
    from app.web.public import crm_webhooks

    app = FastAPI()
    app.include_router(crm_webhooks.router)
    app.dependency_overrides[crm_webhooks.get_db] = lambda: db_session
    return TestClient(app)


def test_subscriber_sync_webhook_hmac(db_session, monkeypatch):
    from app.services import settings_spec

    monkeypatch.setattr(settings_spec, "resolve_value", lambda *a, **k: "testsecret")
    import app.api.subscribers as subs

    monkeypatch.setattr(subs, "_handle_selfcare_webhook", lambda db, payload: {"subscriber_id": "x"})

    client = _webhook_client(db_session)
    body = json.dumps({"id": "123"}).encode()
    url = "/webhooks/crm/subscribers/sync"

    assert client.post(url, content=body).status_code == 401  # missing signature
    assert client.post(url, content=body, headers={"X-Selfcare-Signature": "sha256=bad"}).status_code == 401

    sig = "sha256=" + hmac.new(b"testsecret", body, hashlib.sha256).hexdigest()
    res = client.post(url, content=body, headers={"X-Selfcare-Signature": sig, "Content-Type": "application/json"})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_splynx_webhook_does_not_enrich_remotely(db_session, monkeypatch):
    """Migrated (splynx) pushes must trust the payload, not call back to selfcare.

    Regression: a billing-snapshot payload carries no nested services/billing, so
    include_remote_details=True triggered a selfcare callback keyed on the legacy
    Splynx id, which 404s and dead-letters the push.
    """
    from unittest.mock import MagicMock

    import app.api.subscribers as subs

    mapper = MagicMock(return_value={})
    monkeypatch.setattr(subs, "map_splynx_customer_to_subscriber_data", mapper)
    monkeypatch.setattr(
        subs.subscriber_service,
        "sync_from_external",
        lambda db, system, ext_id, data: type("S", (), {"id": "sub-uuid"})(),
    )

    payload = {"id": "10291", "balance": "83622.50", "currency": "NGN"}
    result = subs._handle_splynx_webhook(db_session, payload)

    assert result["status"] == "ok"
    assert mapper.call_args.kwargs["include_remote_details"] is False


def test_subscriber_sync_webhook_routes_by_external_system(db_session, monkeypatch):
    """HMAC webhook honors payload external_system so splynx/dotmac stay keyed
    correctly instead of all being treated as selfcare."""
    from app.services import settings_spec

    monkeypatch.setattr(settings_spec, "resolve_value", lambda *a, **k: "testsecret")
    import app.api.subscribers as subs

    routed = {}
    monkeypatch.setattr(
        subs, "_handle_splynx_webhook", lambda db, p: routed.setdefault("system", "splynx") or {"subscriber_id": "s"}
    )
    monkeypatch.setattr(
        subs,
        "_handle_selfcare_webhook",
        lambda db, p: routed.setdefault("system", "selfcare") or {"subscriber_id": "x"},
    )

    client = _webhook_client(db_session)
    body = json.dumps({"id": "10291", "external_system": "splynx", "balance": "10.00"}).encode()
    sig = "sha256=" + hmac.new(b"testsecret", body, hashlib.sha256).hexdigest()
    res = client.post(
        "/webhooks/crm/subscribers/sync",
        content=body,
        headers={"X-Selfcare-Signature": sig, "Content-Type": "application/json"},
    )

    assert res.status_code == 200
    assert routed["system"] == "splynx"  # not selfcare


def test_subscriber_sync_webhook_no_secret_503(db_session, monkeypatch):
    from app.services import settings_spec

    monkeypatch.setattr(settings_spec, "resolve_value", lambda *a, **k: None)
    client = _webhook_client(db_session)
    res = client.post("/webhooks/crm/subscribers/sync", content=b"{}", headers={"X-Selfcare-Signature": "sha256=x"})
    assert res.status_code == 503


def test_selfcare_webhook_updates_person_profile(db_session, monkeypatch):
    import app.api.subscribers as subs

    person = Person(
        first_name="Webhook",
        last_name="Customer",
        email="webhook@example.com",
        gender=Gender.unknown,
        metadata_={"selfcare_id": "sc-webhook"},
    )
    db_session.add(person)
    db_session.commit()

    payload = {
        "id": "sc-webhook",
        "subscriber_number": "SUB-WEBHOOK",
        "status": "active",
        "email": "webhook@example.com",
        "date_of_birth": "1988-11-09",
        "gender": "female",
        "nin": "55555555555",
    }

    result = subs._handle_selfcare_webhook(db_session, payload)

    db_session.refresh(person)
    assert result["status"] == "ok"
    assert person.date_of_birth.isoformat() == "1988-11-09"
    assert person.gender == Gender.female
    assert person.nin == "55555555555"


# ── subscriber RBAC gating (#28) ──────────────────────────────────────────────


def _subscribers_client(db_session, auth):
    from app.api.subscribers import router
    from app.services.auth_dependencies import require_user_auth

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[require_user_auth] = lambda: auth
    return TestClient(app)


def test_subscriber_list_denied_without_permission(db_session):
    # Non-admin principal with no granted subscriber permission → 403.
    client = _subscribers_client(db_session, {"person_id": str(uuid4()), "roles": [], "scopes": []})
    assert client.get("/subscribers").status_code == 403


def test_subscriber_list_allowed_for_admin(db_session):
    client = _subscribers_client(db_session, {"person_id": str(uuid4()), "roles": ["admin"], "scopes": []})
    assert client.get("/subscribers").status_code == 200


# ── off-request provisioning entry point (#23) ────────────────────────────────


def test_provision_project_selfcare_skips_missing(db_session):
    from app.services.events.handlers.selfcare_customer import provision_project_selfcare

    result = provision_project_selfcare(db_session, str(uuid4()))
    assert result.get("skipped") == "project_not_found"


# ── billing-risk enrichment cap (#23) ─────────────────────────────────────────


def test_billing_risk_enrich_lookup_cap(monkeypatch):
    from app.web.admin import billing_risk

    calls = {"n": 0}

    def fake_fetch(db, customer_id):
        calls["n"] += 1
        return []

    monkeypatch.setattr(billing_risk, "_billing_risk_row_customer_id", lambda row: row.get("cid"))
    monkeypatch.setattr(billing_risk.selfcare, "fetch_customer_internet_services", fake_fetch)

    rows = [{"plan": "", "cid": str(i)} for i in range(billing_risk._MAX_ENRICH_LOOKUPS + 10)]
    billing_risk._enrich_missing_plan_fields(None, rows)
    assert calls["n"] == billing_risk._MAX_ENRICH_LOOKUPS
