"""Tests for CRM inbox unreplied conversation filter."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.person import Person
from app.services.crm.inbox.queries import get_assignment_counts, get_inbox_stats, list_inbox_conversations


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
    timestamp: datetime | None = None,
) -> Message:
    ts = timestamp or datetime.now(UTC)
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        direction=direction,
        status=MessageStatus.received if direction == MessageDirection.inbound else MessageStatus.sent,
        body=body,
        received_at=ts if direction == MessageDirection.inbound else None,
        sent_at=ts if direction == MessageDirection.outbound else None,
    )
    db_session.add(message)
    conversation.last_message_at = ts
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


def test_needs_attention_filter_returns_customer_follow_ups_after_agent_reply(db_session):
    contact = _create_person(db_session, name="FollowUp")
    now = datetime.now(UTC)

    needs_attention = _create_conversation(db_session, contact, subject="Needs attention")
    settled = _create_conversation(db_session, contact, subject="Agent replied last")
    unresolved_first_touch = _create_conversation(db_session, contact, subject="No agent reply yet")
    resolved_conversation = _create_conversation(db_session, contact, subject="Resolved")
    resolved_conversation.status = ConversationStatus.resolved

    _add_message(
        db_session,
        needs_attention,
        direction=MessageDirection.inbound,
        body="Customer asks question",
        timestamp=now - timedelta(minutes=6),
    )
    _add_message(
        db_session,
        needs_attention,
        direction=MessageDirection.outbound,
        body="Agent answers",
        timestamp=now - timedelta(minutes=5),
    )
    _add_message(
        db_session,
        needs_attention,
        direction=MessageDirection.inbound,
        body="Customer follows up",
        timestamp=now - timedelta(minutes=1),
    )

    _add_message(
        db_session,
        settled,
        direction=MessageDirection.inbound,
        body="Customer asks",
        timestamp=now - timedelta(minutes=5),
    )
    _add_message(
        db_session,
        settled,
        direction=MessageDirection.outbound,
        body="Agent latest reply",
        timestamp=now - timedelta(minutes=2),
    )

    _add_message(
        db_session,
        unresolved_first_touch,
        direction=MessageDirection.inbound,
        body="Need onboarding help",
        timestamp=now - timedelta(minutes=3),
    )

    _add_message(
        db_session,
        resolved_conversation,
        direction=MessageDirection.inbound,
        body="Customer follow-up after resolve should not count",
        timestamp=now - timedelta(minutes=4),
    )
    _add_message(
        db_session,
        resolved_conversation,
        direction=MessageDirection.outbound,
        body="Agent response",
        timestamp=now - timedelta(minutes=3),
    )
    _add_message(
        db_session,
        resolved_conversation,
        direction=MessageDirection.inbound,
        body="Another customer follow-up",
        timestamp=now - timedelta(minutes=1),
    )

    db_session.flush()

    results = list_inbox_conversations(db_session, assignment="needs_attention")
    ids = _result_ids(results)

    assert needs_attention.id in ids
    assert settled.id not in ids
    assert unresolved_first_touch.id not in ids
    assert resolved_conversation.id not in ids


def test_assignment_counts_include_needs_attention_and_unreplied(db_session):
    contact = _create_person(db_session, name="Counts")
    now = datetime.now(UTC)

    unassigned = _create_conversation(db_session, contact, subject="Unassigned")
    _add_message(
        db_session,
        unassigned,
        direction=MessageDirection.inbound,
        body="Need help",
        timestamp=now - timedelta(minutes=2),
    )

    needs_attention = _create_conversation(db_session, contact, subject="Needs attention")
    _add_message(
        db_session,
        needs_attention,
        direction=MessageDirection.inbound,
        body="First message",
        timestamp=now - timedelta(minutes=6),
    )
    _add_message(
        db_session,
        needs_attention,
        direction=MessageDirection.outbound,
        body="Reply",
        timestamp=now - timedelta(minutes=5),
    )
    _add_message(
        db_session,
        needs_attention,
        direction=MessageDirection.inbound,
        body="Follow-up",
        timestamp=now - timedelta(minutes=1),
    )

    counts = get_assignment_counts(db_session, assigned_person_id=None)
    assert counts["unassigned"] >= 2
    assert counts["unreplied"] >= 1
    assert counts["needs_attention"] >= 1


def test_unassigned_filter_excludes_pre_2026_activity(db_session):
    contact = _create_person(db_session, name="Imported")

    imported_legacy = _create_conversation(db_session, contact, subject="Imported 2025")
    current_2026 = _create_conversation(db_session, contact, subject="Current 2026")

    _add_message(
        db_session,
        imported_legacy,
        direction=MessageDirection.inbound,
        body="Legacy imported conversation",
        timestamp=datetime(2025, 12, 31, 23, 59, tzinfo=UTC),
    )
    _add_message(
        db_session,
        current_2026,
        direction=MessageDirection.inbound,
        body="Current conversation",
        timestamp=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    )
    db_session.flush()

    results = list_inbox_conversations(db_session, assignment="unassigned")
    ids = _result_ids(results)

    assert current_2026.id in ids
    assert imported_legacy.id not in ids

    counts = get_assignment_counts(db_session, assigned_person_id=None)
    assert counts["unassigned"] == 1


def test_inbox_unread_stat_counts_customer_awaiting_response(db_session):
    contact = _create_person(db_session, name="UnreadStat")
    now = datetime.now(UTC)

    unreplied = _create_conversation(db_session, contact, subject="Inbound only")
    needs_attention = _create_conversation(db_session, contact, subject="Follow-up pending")
    settled = _create_conversation(db_session, contact, subject="Agent latest reply")
    resolved_pending = _create_conversation(db_session, contact, subject="Resolved should be excluded")
    resolved_pending.status = ConversationStatus.resolved

    _add_message(
        db_session,
        unreplied,
        direction=MessageDirection.inbound,
        body="First inbound",
        timestamp=now - timedelta(minutes=5),
    )

    _add_message(
        db_session,
        needs_attention,
        direction=MessageDirection.inbound,
        body="Inbound 1",
        timestamp=now - timedelta(minutes=8),
    )
    _add_message(
        db_session,
        needs_attention,
        direction=MessageDirection.outbound,
        body="Agent reply",
        timestamp=now - timedelta(minutes=6),
    )
    _add_message(
        db_session,
        needs_attention,
        direction=MessageDirection.inbound,
        body="Customer follow-up",
        timestamp=now - timedelta(minutes=1),
    )

    _add_message(
        db_session,
        settled,
        direction=MessageDirection.inbound,
        body="Inbound",
        timestamp=now - timedelta(minutes=6),
    )
    _add_message(
        db_session,
        settled,
        direction=MessageDirection.outbound,
        body="Latest agent response",
        timestamp=now - timedelta(minutes=2),
    )

    _add_message(
        db_session,
        resolved_pending,
        direction=MessageDirection.inbound,
        body="Resolved but inbound latest",
        timestamp=now - timedelta(minutes=7),
    )
    _add_message(
        db_session,
        resolved_pending,
        direction=MessageDirection.outbound,
        body="Resolved reply",
        timestamp=now - timedelta(minutes=5),
    )
    _add_message(
        db_session,
        resolved_pending,
        direction=MessageDirection.inbound,
        body="Inbound again",
        timestamp=now - timedelta(minutes=1),
    )

    stats = get_inbox_stats(db_session)
    assert stats["unread"] == 2
