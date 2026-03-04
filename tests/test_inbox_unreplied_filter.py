"""Tests for CRM inbox unreplied conversation filter."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.person import Person
from app.services.crm.inbox.queries import list_inbox_conversations


def _unique_email() -> str:
    return f"unreplied-filter-{uuid.uuid4().hex[:8]}@example.com"


def _create_person(db_session, *, name: str = "Test") -> Person:
    person = Person(first_name=name, last_name="Contact", email=_unique_email())
    db_session.add(person)
    db_session.flush()
    return person


def _create_conversation(db_session, contact: Person, *, subject: str) -> Conversation:
    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.open,
        subject=subject,
    )
    db_session.add(conversation)
    db_session.flush()
    return conversation


def _add_message(
    db_session,
    conversation: Conversation,
    *,
    direction: MessageDirection,
    body: str,
) -> Message:
    timestamp = datetime.now(UTC)
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        direction=direction,
        status=MessageStatus.received if direction == MessageDirection.inbound else MessageStatus.sent,
        body=body,
        received_at=timestamp if direction == MessageDirection.inbound else None,
        sent_at=timestamp if direction == MessageDirection.outbound else None,
    )
    db_session.add(message)
    conversation.last_message_at = timestamp
    db_session.flush()
    return message


def _result_ids(results: list[tuple]) -> set[uuid.UUID]:
    return {row[0].id for row in results}


def test_unreplied_filter_returns_inbound_without_outbound(db_session):
    contact = _create_person(db_session, name="Customer")

    unreplied = _create_conversation(db_session, contact, subject="Inbound only")
    replied = _create_conversation(db_session, contact, subject="Inbound and outbound")
    outbound_only = _create_conversation(db_session, contact, subject="Outbound only")
    internal_only = _create_conversation(db_session, contact, subject="Internal only")

    _add_message(
        db_session,
        unreplied,
        direction=MessageDirection.inbound,
        body="Need help with my router",
    )

    _add_message(
        db_session,
        replied,
        direction=MessageDirection.inbound,
        body="My internet is down",
    )
    _add_message(
        db_session,
        replied,
        direction=MessageDirection.outbound,
        body="We are checking this for you",
    )

    _add_message(
        db_session,
        outbound_only,
        direction=MessageDirection.outbound,
        body="Following up on your request",
    )

    _add_message(
        db_session,
        internal_only,
        direction=MessageDirection.internal,
        body="Internal note",
    )

    results = list_inbox_conversations(db_session, assignment="unreplied")
    ids = _result_ids(results)

    assert unreplied.id in ids
    assert replied.id not in ids
    assert outbound_only.id not in ids
    assert internal_only.id not in ids
