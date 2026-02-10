from datetime import UTC, datetime

import pytest

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection
from app.models.person import ChannelType as PersonChannelType
from app.models.person import PersonChannel
from app.services.crm.inbox import dedup


def _create_conversation(db_session, person):
    conversation = Conversation(person_id=person.id)
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)
    return conversation


def _create_person_channel(db_session, person, address):
    channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.email,
        address=address,
        is_primary=True,
    )
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)
    return channel


def test_build_inbound_dedupe_id_email_case_insensitive():
    received_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    first = dedup._build_inbound_dedupe_id(
        ChannelType.email,
        "TEST@Example.com",
        "Hello",
        "Body",
        received_at,
        source_id="msg-1",
    )
    second = dedup._build_inbound_dedupe_id(
        ChannelType.email,
        "test@example.com",
        "Hello",
        "Body",
        received_at,
        source_id="msg-1",
    )
    assert first == second


def test_find_duplicate_inbound_message_by_external_id(db_session, person):
    conversation = _create_conversation(db_session, person)
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.inbound,
        external_id="msg-123",
        body="Hello",
        received_at=datetime.now(UTC),
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)

    found = dedup._find_duplicate_inbound_message(
        db_session,
        ChannelType.email,
        None,
        None,
        "msg-123",
        subject=None,
        body="Hello",
        received_at=message.received_at,
    )
    assert found is not None
    assert found.id == message.id


def test_find_duplicate_inbound_message_fallback(db_session, person):
    conversation = _create_conversation(db_session, person)
    channel = _create_person_channel(db_session, person, "user@example.com")
    received_at = datetime.now(UTC)
    message = Message(
        conversation_id=conversation.id,
        person_channel_id=channel.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.inbound,
        subject="Subject",
        body="Hello",
        received_at=received_at,
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)

    found = dedup._find_duplicate_inbound_message(
        db_session,
        ChannelType.email,
        channel.id,
        None,
        None,
        subject="Subject",
        body="Hello",
        received_at=received_at,
    )
    assert found is not None
    assert found.id == message.id
