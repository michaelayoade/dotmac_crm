"""Vendor project-stub relay receiver (Phase 3, risk #6): HMAC auth, upsert,
idempotency, and no-clobber of CRM-native project rows."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.projects import Project, ProjectStatus, ProjectType
from app.services import vendor_project_relay as relay

RELAY_URL = "/webhooks/crm/projects/relay"


def _webhook_client(db_session):
    from app.web.public import crm_webhooks

    app = FastAPI()
    app.include_router(crm_webhooks.router)
    app.dependency_overrides[crm_webhooks.get_db] = lambda: db_session
    return TestClient(app)


def _signed(body: bytes, secret: bytes = b"testsecret") -> dict[str, str]:
    sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return {"X-Selfcare-Signature": sig, "Content-Type": "application/json"}


def _stub(project_id, **over):
    payload = {
        "id": str(project_id),
        "name": "Native install",
        "status": "active",
        "project_type": "fiber_optics_installation",
        "customer_address": "12 Fiber Rd, Abuja",
        "region": "Abuja",
        "subscriber_external_ref": "crm-sub-1",
        "source": "sub_relay",
    }
    payload.update(over)
    return payload


# ── service: upsert / idempotency / no-clobber ────────────────────────────────


def test_upsert_creates_stub(db_session):
    pid = uuid.uuid4()
    result = relay.upsert_project_stub(db_session, _stub(pid))
    assert result == {"action": "created", "project_id": str(pid)}

    row = db_session.get(Project, pid)
    assert row is not None
    assert row.id == pid  # id == sub UUID (shared-UUID strategy)
    assert row.name == "Native install"
    assert row.status == ProjectStatus.active
    assert row.project_type == ProjectType.fiber_optics_installation
    assert row.region == "Abuja"
    assert row.metadata_["source"] == "sub_relay"
    assert row.metadata_["sub_subscriber_ref"] == "crm-sub-1"


def test_upsert_is_idempotent_and_updates_stub_fields(db_session):
    pid = uuid.uuid4()
    relay.upsert_project_stub(db_session, _stub(pid))
    # Re-push with changed stub fields (status advanced, address updated).
    result = relay.upsert_project_stub(
        db_session,
        _stub(pid, status="completed", customer_address="99 New Rd"),
    )
    assert result == {"action": "updated", "project_id": str(pid)}

    rows = db_session.query(Project).filter(Project.id == pid).all()
    assert len(rows) == 1  # idempotent — no duplicate row
    assert rows[0].status == ProjectStatus.completed
    assert rows[0].customer_address == "99 New Rd"


def test_upsert_does_not_clobber_native_row(db_session):
    """A CRM-native project (no sub_relay marker) is never overwritten."""
    pid = uuid.uuid4()
    native = Project(
        id=pid,
        name="CRM-native project",
        status=ProjectStatus.planned,
        project_type=ProjectType.cross_connect,
        customer_address="native address",
        metadata_={"source": "crm_admin"},
    )
    db_session.add(native)
    db_session.commit()

    result = relay.upsert_project_stub(db_session, _stub(pid, name="RELAY OVERWRITE"))
    assert result == {"action": "skipped_native", "project_id": str(pid)}

    row = db_session.get(Project, pid)
    assert row.name == "CRM-native project"  # untouched
    assert row.status == ProjectStatus.planned
    assert row.customer_address == "native address"


def test_upsert_missing_id_raises(db_session):
    import pytest

    with pytest.raises(relay.RelayPayloadError):
        relay.upsert_project_stub(db_session, {"name": "x"})


def test_upsert_missing_name_raises(db_session):
    import pytest

    with pytest.raises(relay.RelayPayloadError):
        relay.upsert_project_stub(db_session, {"id": str(uuid.uuid4())})


def test_upsert_unknown_type_becomes_null(db_session):
    pid = uuid.uuid4()
    relay.upsert_project_stub(db_session, _stub(pid, project_type="legacy_unknown"))
    assert db_session.get(Project, pid).project_type is None


# ── endpoint: HMAC auth ───────────────────────────────────────────────────────


def test_endpoint_requires_signature(db_session, monkeypatch):
    from app.services import settings_spec

    monkeypatch.setattr(settings_spec, "resolve_value", lambda *a, **k: "testsecret")
    client = _webhook_client(db_session)
    body = json.dumps(_stub(uuid.uuid4())).encode()

    assert client.post(RELAY_URL, content=body).status_code == 401
    assert client.post(RELAY_URL, content=body, headers={"X-Selfcare-Signature": "sha256=bad"}).status_code == 401


def test_endpoint_accepts_valid_signature_and_upserts(db_session, monkeypatch):
    from app.services import settings_spec

    monkeypatch.setattr(settings_spec, "resolve_value", lambda *a, **k: "testsecret")
    client = _webhook_client(db_session)
    pid = uuid.uuid4()
    body = json.dumps(_stub(pid)).encode()

    res = client.post(RELAY_URL, content=body, headers=_signed(body))
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "action": "created", "project_id": str(pid)}
    assert db_session.get(Project, pid) is not None


def test_endpoint_no_secret_503(db_session, monkeypatch):
    from app.services import settings_spec

    monkeypatch.setattr(settings_spec, "resolve_value", lambda *a, **k: None)
    client = _webhook_client(db_session)
    body = json.dumps(_stub(uuid.uuid4())).encode()
    res = client.post(RELAY_URL, content=body, headers=_signed(body))
    assert res.status_code == 503


def test_endpoint_bad_payload_400(db_session, monkeypatch):
    from app.services import settings_spec

    monkeypatch.setattr(settings_spec, "resolve_value", lambda *a, **k: "testsecret")
    client = _webhook_client(db_session)
    body = json.dumps({"name": "no id"}).encode()
    res = client.post(RELAY_URL, content=body, headers=_signed(body))
    assert res.status_code == 400
