"""Tests for CRM conversation data quality check service."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation, ConversationTag
from app.models.crm.enums import ConversationStatus
from app.models.person import Person
from app.services.crm.inbox.data_quality import check_data_quality


def _make_person(db):
    """Create a Person with a unique email."""
    p = Person(
        first_name="Test",
        last_name="User",
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
    )
    db.add(p)
    db.flush()
    return p


def test_flags_resolved_without_first_response(db_session):
    person = _make_person(db_session)
    conv = Conversation(
        person_id=person.id,
        status=ConversationStatus.resolved,
        resolved_at=datetime.now(UTC) - timedelta(hours=2),
        first_response_at=None,
    )
    db_session.add(conv)
    db_session.flush()

    result = check_data_quality(db_session)
    assert result["missing_first_response"] >= 1


def test_flags_resolved_without_tags(db_session):
    person = _make_person(db_session)
    conv = Conversation(
        person_id=person.id,
        status=ConversationStatus.resolved,
        resolved_at=datetime.now(UTC) - timedelta(hours=2),
        first_response_at=datetime.now(UTC) - timedelta(hours=3),
    )
    db_session.add(conv)
    db_session.flush()

    result = check_data_quality(db_session)
    assert result["missing_tags"] >= 1


def test_does_not_flag_tagged_conversation(db_session):
    person = _make_person(db_session)
    conv = Conversation(
        person_id=person.id,
        status=ConversationStatus.resolved,
        resolved_at=datetime.now(UTC) - timedelta(hours=2),
        first_response_at=datetime.now(UTC) - timedelta(hours=3),
    )
    db_session.add(conv)
    db_session.flush()

    tag = ConversationTag(conversation_id=conv.id, tag="support")
    db_session.add(tag)
    db_session.flush()

    result = check_data_quality(db_session)
    assert result["missing_tags"] == 0
