"""Shared utilities for inbound handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import MessageDirection, MessageStatus
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox.context import get_inbox_logger
from app.services.crm.inbox.notifications import notify_assigned_agent_new_reply
from app.services.crm.inbox.routing import apply_routing_rules
from app.websocket.broadcaster import (
    broadcast_conversation_summary,
    broadcast_inbox_updated,
    broadcast_new_message,
)

logger = get_inbox_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _message_order_timestamp(message: Message) -> datetime:
    """Use receive/send time when present, but never older than DB creation time."""
    base = message.received_at or message.sent_at or message.created_at or _now()
    # created_at may not be populated before flush; use current time as floor.
    created = message.created_at or _now()
    return created if created > base else base


def build_conversation_summary(db: Session, conversation: Conversation, message: Message) -> dict:
    unread_count = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.status == MessageStatus.received)
        .filter(Message.read_at.is_(None))
        .count()
    )
    last_message_at = _message_order_timestamp(message)
    preview = message.body or ""
    if len(preview) > 100:
        preview = preview[:100] + "..."
    return {
        "preview": preview,
        "last_message_at": last_message_at.isoformat() if last_message_at else None,
        "channel": message.channel_type.value if message.channel_type else None,
        "unread_count": unread_count,
    }


def post_process_inbound_message(
    db: Session,
    *,
    conversation_id: str,
    message_id: str,
    channel_target_id: str | None,
) -> None:
    try:
        conversation = db.get(Conversation, conversation_id)
        message = db.get(Message, message_id)
        if not conversation or not message:
            return

        if message.direction == MessageDirection.inbound:
            apply_routing_rules(db, conversation=conversation, message=message)
        broadcast_new_message(message, conversation)
        notify_assigned_agent_new_reply(db, conversation, message)
        broadcast_conversation_summary(
            str(conversation.id),
            build_conversation_summary(db, conversation, message),
        )

        # Notify agents assigned to this conversation or on the same team
        agent_person_ids: set[str] = set()
        assignments = (
            db.query(ConversationAssignment.agent_id, ConversationAssignment.team_id)
            .filter(ConversationAssignment.conversation_id == conversation.id)
            .filter(ConversationAssignment.is_active.is_(True))
            .all()
        )
        assigned_agent_ids = {a.agent_id for a in assignments if a.agent_id}
        team_ids = {a.team_id for a in assignments if a.team_id}

        # Batch-load team members
        if team_ids:
            team_agent_ids = {
                row[0]
                for row in db.query(CrmAgentTeam.agent_id)
                .filter(CrmAgentTeam.team_id.in_(team_ids))
                .filter(CrmAgentTeam.is_active.is_(True))
                .all()
            }
            assigned_agent_ids |= team_agent_ids

        # Batch-load person_ids for all agents in one query
        if assigned_agent_ids:
            agent_rows = (
                db.query(CrmAgent.person_id)
                .filter(CrmAgent.id.in_(assigned_agent_ids))
                .filter(CrmAgent.person_id.isnot(None))
                .all()
            )
            agent_person_ids = {str(row[0]) for row in agent_rows}
        # Fallback: if no specific agents, notify all active agents
        if not agent_person_ids:
            all_agents = (
                db.query(CrmAgent.person_id)
                .filter(CrmAgent.is_active.is_(True))
                .filter(CrmAgent.person_id.isnot(None))
                .distinct()
                .all()
            )
            agent_person_ids = {str(row[0]) for row in all_agents}
        inbox_payload = {
            "conversation_id": str(conversation.id),
            "message_id": str(message.id),
            "channel_target_id": channel_target_id,
            "last_message_at": _message_order_timestamp(message).isoformat(),
        }
        for person_id in agent_person_ids:
            broadcast_inbox_updated(person_id, inbox_payload)

        inbox_cache.invalidate_inbox_list()
    except Exception as exc:
        logger.warning("inbound_post_process_failed error=%s", exc)


def create_message_and_touch_conversation(
    db: Session,
    *,
    conversation_id,
    payload: dict[str, Any],
) -> tuple[Conversation, Message]:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")

    message = Message(**payload)
    if message.direction == MessageDirection.inbound and not message.received_at:
        message.received_at = _now()
    if message.direction == MessageDirection.outbound and not message.sent_at:
        message.sent_at = _now()
    db.add(message)
    timestamp = _message_order_timestamp(message)
    conversation.last_message_at = timestamp
    conversation.updated_at = timestamp
    db.flush()
    return conversation, message
