"""Tests for waiting-queue per-channel stats used on the admin dashboard."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.person import Person
from app.services.crm.inbox.queries import get_waiting_queue_counts_by_channel


def _mk_person(*, email: str) -> Person:
    return Person(
        first_name="Test",
        last_name="User",
        display_name="Test User",
        email=email,
    )


def test_get_waiting_queue_counts_by_channel_counts_open_and_snoozed_only(db_session):
    p1 = _mk_person(email="p1@example.com")
    p2 = _mk_person(email="p2@example.com")
    p3 = _mk_person(email="p3@example.com")
    db_session.add_all([p1, p2, p3])
    db_session.flush()

    conv_open_email = Conversation(person_id=p1.id, status=ConversationStatus.open, is_active=True)
    conv_snoozed_whatsapp = Conversation(person_id=p2.id, status=ConversationStatus.snoozed, is_active=True)
    conv_resolved_email = Conversation(person_id=p3.id, status=ConversationStatus.resolved, is_active=True)
    db_session.add_all([conv_open_email, conv_snoozed_whatsapp, conv_resolved_email])
    db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            Message(
                conversation_id=conv_open_email.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                status=MessageStatus.received,
                body="hello",
                received_at=now,
            ),
            Message(
                conversation_id=conv_snoozed_whatsapp.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                status=MessageStatus.received,
                body="hi",
                received_at=now,
            ),
            Message(
                conversation_id=conv_resolved_email.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                status=MessageStatus.received,
                body="resolved",
                received_at=now,
            ),
        ]
    )
    db_session.commit()

    counts = get_waiting_queue_counts_by_channel(db_session)
    assert counts["email"] == 1
    assert counts["whatsapp"] == 1


def test_get_waiting_queue_counts_by_channel_can_count_a_conversation_in_multiple_channels(db_session):
    p1 = _mk_person(email="multi@example.com")
    db_session.add(p1)
    db_session.flush()

    conv = Conversation(person_id=p1.id, status=ConversationStatus.open, is_active=True)
    db_session.add(conv)
    db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all(
        [
            Message(
                conversation_id=conv.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                status=MessageStatus.received,
                body="email msg",
                received_at=now,
            ),
            Message(
                conversation_id=conv.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                status=MessageStatus.received,
                body="wa msg",
                received_at=now,
            ),
        ]
    )
    db_session.commit()

    counts = get_waiting_queue_counts_by_channel(db_session)
    assert counts["email"] == 1
    assert counts["whatsapp"] == 1
