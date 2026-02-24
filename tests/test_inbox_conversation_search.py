"""Tests for inbox conversation search matching email and phone addresses."""

from __future__ import annotations

import uuid

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationStatus
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel
from app.services.crm.inbox.queries import list_inbox_conversations


def _unique_email() -> str:
    return f"search-{uuid.uuid4().hex[:8]}@example.com"


def _create_person(db_session, *, email: str | None = None, phone: str | None = None) -> Person:
    person = Person(first_name="Search", last_name="Contact", email=email or _unique_email(), phone=phone)
    db_session.add(person)
    db_session.flush()
    return person


def _create_conversation(db_session, person: Person, *, subject: str) -> Conversation:
    conversation = Conversation(person_id=person.id, status=ConversationStatus.open, subject=subject)
    db_session.add(conversation)
    db_session.flush()
    return conversation


def test_search_matches_person_channel_email(db_session):
    person = _create_person(db_session)
    conversation = _create_conversation(db_session, person, subject="Email channel search")

    channel_email = f"alias-{uuid.uuid4().hex[:6]}@example.com"
    db_session.add(
        PersonChannel(
            person_id=person.id,
            channel_type=PersonChannelType.email,
            address=channel_email,
            is_primary=False,
        )
    )
    db_session.flush()

    results = list_inbox_conversations(db_session, search=channel_email)
    ids = {row[0].id for row in results}
    assert conversation.id in ids


def test_search_matches_person_phone_with_format_variants(db_session):
    person = _create_person(db_session, phone="+15551234567")
    conversation = _create_conversation(db_session, person, subject="Phone normalization search")

    results = list_inbox_conversations(db_session, search="(555) 123-4567")
    ids = {row[0].id for row in results}
    assert conversation.id in ids


def test_search_matches_person_channel_phone_with_format_variants(db_session):
    person = _create_person(db_session)
    conversation = _create_conversation(db_session, person, subject="Channel phone normalization search")

    db_session.add(
        PersonChannel(
            person_id=person.id,
            channel_type=PersonChannelType.whatsapp,
            address="+15559876543",
            is_primary=True,
        )
    )
    db_session.flush()

    results = list_inbox_conversations(db_session, search="555-987-6543")
    ids = {row[0].id for row in results}
    assert conversation.id in ids
