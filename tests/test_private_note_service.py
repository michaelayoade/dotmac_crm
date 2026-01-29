import os

import pytest
from sqlalchemy import create_engine
from fastapi import HTTPException

from app.logic import private_note_logic
from app.models.crm.enums import ChannelType, MessageDirection
from app.schemas.crm.conversation import ConversationCreate
from app.services.crm import conversation as conversation_service
from app.services.crm import private_notes as private_notes_service


def _create_conversation(db_session, person):
    return conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=person.id),
    )


def _dialect_name() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return create_engine(url).dialect.name
    return "sqlite"


_SKIP_SQLITE = _dialect_name() == "sqlite"


@pytest.mark.skipif(
    _SKIP_SQLITE,
    reason="SQLite lacks spatialite functions used by this test DB setup",
)
def test_send_private_note_allowed(db_session, person, monkeypatch):
    monkeypatch.setattr(private_note_logic, "USE_PRIVATE_NOTE_LOGIC_SERVICE", True)
    conversation = _create_conversation(db_session, person)

    message = private_notes_service.send_private_note(
        db_session,
        str(conversation.id),
        str(person.id),
        "Internal update for the team.",
        "team",
    )

    assert message.direction == MessageDirection.internal
    assert message.channel_type == ChannelType.note
    assert message.metadata_.get("type") == "private_note"
    assert message.metadata_.get("visibility") == "team"


@pytest.mark.skipif(
    _SKIP_SQLITE,
    reason="SQLite lacks spatialite functions used by this test DB setup",
)
def test_send_private_note_denied_empty_body(db_session, person, monkeypatch):
    monkeypatch.setattr(private_note_logic, "USE_PRIVATE_NOTE_LOGIC_SERVICE", True)
    conversation = _create_conversation(db_session, person)

    with pytest.raises(HTTPException) as exc_info:
        private_notes_service.send_private_note(
            db_session,
            str(conversation.id),
            str(person.id),
            "   ",
            "team",
        )

    assert exc_info.value.status_code == 400
