import json
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.logic import private_note_logic
from app.web import admin as admin_module
from app.web.admin import crm as crm_routes


def _make_request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/admin/crm/inbox/conversation/conv/note",
        "headers": [],
    }
    return Request(scope)


def test_create_private_note_allowed(monkeypatch):
    author_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(private_note_logic, "USE_PRIVATE_NOTE_LOGIC_SERVICE", True)
    monkeypatch.setattr(admin_module, "get_current_user", lambda request: {"person_id": author_id})
    monkeypatch.setattr(crm_routes.conversation_service.Conversations, "get", lambda db, cid: object())

    note = SimpleNamespace(
        id="note-1",
        conversation_id="conv-1",
        author_id=author_id,
        body="Internal update.",
        metadata_={"type": "private_note", "visibility": "team"},
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    monkeypatch.setattr(
        __import__("app.services.crm.private_notes", fromlist=["send_private_note"]),
        "send_private_note",
        lambda **kwargs: note,
    )

    payload = crm_routes.PrivateNoteCreate(
        body="Internal update.",
        requested_visibility="team",
    )
    response = crm_routes.create_private_note(
        request=_make_request(),
        conversation_id="conv-1",
        payload=payload,
        db=None,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 200
    data = json.loads(response.body)
    assert data["conversation_id"] == "conv-1"
    assert data["visibility"] == "team"


def test_create_private_note_denied_empty_body(monkeypatch):
    author_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(private_note_logic, "USE_PRIVATE_NOTE_LOGIC_SERVICE", True)
    monkeypatch.setattr(admin_module, "get_current_user", lambda request: {"person_id": author_id})
    monkeypatch.setattr(crm_routes.conversation_service.Conversations, "get", lambda db, cid: object())
    monkeypatch.setattr(
        __import__("app.services.crm.private_notes", fromlist=["send_private_note"]),
        "send_private_note",
        lambda **kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=400, detail="Private note body is empty")
        ),
    )

    payload = crm_routes.PrivateNoteCreate(
        body="   ",
        requested_visibility="team",
    )
    response = crm_routes.create_private_note(
        request=_make_request(),
        conversation_id="conv-1",
        payload=payload,
        db=None,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    data = json.loads(response.body)
    assert "detail" in data
