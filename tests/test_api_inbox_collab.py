"""Inbox collaboration API — private notes CRUD + message attachments."""

import uuid

import pytest
from fastapi import HTTPException

from app.api.crm import inbox_collab as collab


def test_private_note_lifecycle(db_session, crm_conversation_factory):
    conv = crm_conversation_factory()
    auth = {"person_id": str(conv.person_id)}

    assert collab.list_private_notes(str(conv.id), db_session) == []

    note = collab.create_private_note(
        str(conv.id), collab.PrivateNoteCreate(body="Internal heads-up"), db_session, auth
    )
    assert note["body"] == "Internal heads-up"
    note_id = note["id"]

    notes = collab.list_private_notes(str(conv.id), db_session)
    assert len(notes) == 1

    # the note is itself a message → it has an (empty) attachment list
    assert collab.list_message_attachments(note_id, db_session) == []

    collab.remove_private_note(str(conv.id), note_id, db_session, auth)
    assert collab.list_private_notes(str(conv.id), db_session) == []


def test_attachments_missing_message_404(db_session):
    with pytest.raises(HTTPException) as exc:
        collab.list_message_attachments(str(uuid.uuid4()), db_session)
    assert exc.value.status_code == 404
