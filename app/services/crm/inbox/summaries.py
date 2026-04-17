"""Conversation summary maintenance for fast inbox reads."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, ConversationSummary, Message
from app.models.crm.enums import ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.outbox import OutboxMessage
from app.services.common import coerce_uuid


def _now() -> datetime:
    return datetime.now(UTC)


def _message_activity_ts():
    return func.coalesce(Message.received_at, Message.sent_at, Message.created_at)


def recompute_conversation_summary(db: Session, conversation_id: str) -> ConversationSummary | None:
    """Recompute the summary row for one conversation inside the caller's transaction."""
    conversation_uuid = coerce_uuid(str(conversation_id))
    conversation = db.get(Conversation, conversation_uuid)
    if not conversation:
        summary = db.get(ConversationSummary, conversation_uuid)
        if summary:
            db.delete(summary)
        return None

    latest = (
        db.query(Message.id, Message.channel_type, _message_activity_ts().label("activity_at"))
        .filter(Message.conversation_id == conversation_uuid)
        .order_by(_message_activity_ts().desc())
        .first()
    )
    latest_inbound_at = (
        db.query(func.max(_message_activity_ts()))
        .filter(Message.conversation_id == conversation_uuid)
        .filter(Message.direction == MessageDirection.inbound)
        .scalar()
    )
    latest_outbound_at = (
        db.query(func.max(_message_activity_ts()))
        .filter(Message.conversation_id == conversation_uuid)
        .filter(Message.direction == MessageDirection.outbound)
        .scalar()
    )
    unread_count = (
        db.query(func.count(Message.id))
        .filter(Message.conversation_id == conversation_uuid)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.status == MessageStatus.received)
        .filter(Message.read_at.is_(None))
        .scalar()
        or 0
    )
    has_inbound = latest_inbound_at is not None
    has_outbound = latest_outbound_at is not None
    needs_attention = bool(
        conversation.status != ConversationStatus.resolved
        and latest_inbound_at is not None
        and latest_outbound_at is not None
        and latest_inbound_at > latest_outbound_at
    )
    unreplied = bool(conversation.status != ConversationStatus.resolved and has_inbound and not has_outbound)
    assignment = (
        db.query(ConversationAssignment.agent_id, ConversationAssignment.team_id)
        .filter(ConversationAssignment.conversation_id == conversation_uuid)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    has_failed_outbox = bool(
        db.query(OutboxMessage.id)
        .filter(OutboxMessage.conversation_id == conversation_uuid)
        .filter(OutboxMessage.status == "failed")
        .first()
    )

    summary = db.get(ConversationSummary, conversation_uuid)
    if not summary:
        summary = ConversationSummary(conversation_id=conversation_uuid, person_id=conversation.person_id)
        db.add(summary)

    summary.person_id = conversation.person_id
    summary.latest_message_id = latest.id if latest else None
    summary.latest_message_at = latest.activity_at if latest else conversation.last_message_at
    summary.latest_inbound_at = latest_inbound_at
    summary.latest_outbound_at = latest_outbound_at
    summary.unread_count = int(unread_count)
    summary.has_failed_outbox = has_failed_outbox
    summary.primary_channel_type = latest.channel_type if latest else None
    summary.active_assignment_agent_id = assignment.agent_id if assignment else None
    summary.active_assignment_team_id = assignment.team_id if assignment else None
    summary.needs_attention = needs_attention
    summary.unreplied = unreplied
    summary.status = conversation.status
    summary.priority = conversation.priority
    summary.is_active = bool(conversation.is_active)
    summary.updated_at = _now()
    return summary


def recompute_conversation_summary_and_invalidate_cache(
    db: Session,
    conversation_id: str,
) -> ConversationSummary | None:
    """Recompute summary and clear inbox cache for immediate read-after-write consistency."""
    summary = recompute_conversation_summary(db, conversation_id)
    from app.services.crm.inbox import cache as inbox_cache

    inbox_cache.invalidate_inbox_list()
    return summary


def recompute_conversation_summaries(db: Session, conversation_ids: Iterable[str]) -> int:
    updated = 0
    for conversation_id in {str(value) for value in conversation_ids if value}:
        if recompute_conversation_summary(db, conversation_id) is not None:
            updated += 1
    return updated
