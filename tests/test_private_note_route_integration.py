from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from app.logic import private_note_logic
from app.web import admin as admin_module
from app.web.admin import crm as crm_routes


def _override_admin_auth():
    app.dependency_overrides[admin_module.require_web_auth_or_meta_callback] = (
        lambda request, db=None: {}
    )


def _override_db():
    def _get_db_override():
        yield SimpleNamespace(get=lambda *args, **kwargs: None)

    app.dependency_overrides[crm_routes.get_db] = _get_db_override


def _set_conversation_exists(monkeypatch):
    monkeypatch.setattr(crm_routes.conversation_service.Conversations, "get", lambda db, cid: object())


def _clear_overrides():
    app.dependency_overrides.clear()


def _disable_startup_events():
    original_startup = list(app.router.on_startup)
    original_shutdown = list(app.router.on_shutdown)
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    return original_startup, original_shutdown


def _restore_startup_events(original_startup, original_shutdown):
    app.router.on_startup[:] = original_startup
    app.router.on_shutdown[:] = original_shutdown


def test_private_note_create_allowed(monkeypatch):
    _override_admin_auth()
    _override_db()
    _set_conversation_exists(monkeypatch)
    original_startup, original_shutdown = _disable_startup_events()
    monkeypatch.setattr(private_note_logic, "USE_PRIVATE_NOTE_LOGIC_SERVICE", True)
    monkeypatch.setattr(admin_module, "get_current_user", lambda request: {"person_id": "author-1"})

    def _send_private_note(**kwargs):
        received_at = datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)
        return SimpleNamespace(
            id="note-1",
            conversation_id=kwargs["conversation_id"],
            author_id=kwargs["author_id"],
            body=kwargs["body"],
            metadata_={"type": "private_note", "visibility": kwargs["requested_visibility"]},
            received_at=received_at,
            created_at=received_at,
        )

    monkeypatch.setattr(
        __import__("app.services.crm.private_notes", fromlist=["send_private_note"]),
        "send_private_note",
        _send_private_note,
    )

    client = TestClient(app)
    token = "test-csrf-token"
    client.cookies.set(CSRF_COOKIE_NAME, token)
    response = client.post(
        "/admin/crm/inbox/conv-1/private_note",
        json={"body": "Internal update", "visibility": "team"},
        headers={CSRF_HEADER_NAME: token, "accept": "application/json"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["conversation_id"] == "conv-1"
    assert data["visibility"] == "team"
    assert data["type"] == "private_note"
    assert data["received_at"] == "2024-01-01T09:30:00+00:00"

    _clear_overrides()
    _restore_startup_events(original_startup, original_shutdown)


def test_private_note_create_denied_empty_body(monkeypatch):
    _override_admin_auth()
    _override_db()
    _set_conversation_exists(monkeypatch)
    original_startup, original_shutdown = _disable_startup_events()
    monkeypatch.setattr(private_note_logic, "USE_PRIVATE_NOTE_LOGIC_SERVICE", True)
    monkeypatch.setattr(admin_module, "get_current_user", lambda request: {"person_id": "author-1"})

    def _send_private_note(**kwargs):
        if not kwargs["body"] or not kwargs["body"].strip():
            raise HTTPException(status_code=400, detail="Private note body is empty")
        return None

    monkeypatch.setattr(
        __import__("app.services.crm.private_notes", fromlist=["send_private_note"]),
        "send_private_note",
        _send_private_note,
    )

    client = TestClient(app)
    token = "test-csrf-token"
    client.cookies.set(CSRF_COOKIE_NAME, token)
    response = client.post(
        "/admin/crm/inbox/conv-1/private_note",
        json={"body": "   ", "visibility": "team"},
        headers={CSRF_HEADER_NAME: token, "accept": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Private note body is empty"

    _clear_overrides()
    _restore_startup_events(original_startup, original_shutdown)


def test_private_note_create_denied_system_conversation(monkeypatch):
    _override_admin_auth()
    _override_db()
    _set_conversation_exists(monkeypatch)
    original_startup, original_shutdown = _disable_startup_events()
    monkeypatch.setattr(private_note_logic, "USE_PRIVATE_NOTE_LOGIC_SERVICE", True)
    monkeypatch.setattr(admin_module, "get_current_user", lambda request: {"person_id": "author-1"})

    def _send_private_note(**kwargs):
        raise HTTPException(
            status_code=400,
            detail="Private notes are not allowed for system conversations",
        )

    monkeypatch.setattr(
        __import__("app.services.crm.private_notes", fromlist=["send_private_note"]),
        "send_private_note",
        _send_private_note,
    )

    client = TestClient(app)
    token = "test-csrf-token"
    client.cookies.set(CSRF_COOKIE_NAME, token)
    response = client.post(
        "/admin/crm/inbox/sys-1/private_note",
        json={"body": "Internal note", "visibility": "admins"},
        headers={CSRF_HEADER_NAME: token, "accept": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Private notes are not allowed for system conversations"

    _clear_overrides()
    _restore_startup_events(original_startup, original_shutdown)
