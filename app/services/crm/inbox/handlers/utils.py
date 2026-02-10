"""Shared utilities for inbound handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import MessageDirection, MessageStatus
from app.models.crm.team import CrmAgent
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


def build_conversation_summary(db: Session, conversation: Conversation, message: Message) -> dict:
    unread_count = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.status == MessageStatus.received)
        .filter(Message.read_at.is_(None))
        .count()
    )
    last_message_at = message.received_at or message.sent_at or message.created_at
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

        agent_ids = (
            db.query(CrmAgent.person_id)
            .filter(CrmAgent.is_active.is_(True))
            .filter(CrmAgent.person_id.isnot(None))
            .distinct()
            .all()
        )
        inbox_payload = {
            "conversation_id": str(conversation.id),
            "message_id": str(message.id),
            "channel_target_id": channel_target_id,
            "last_message_at": (
                (message.received_at or message.created_at).isoformat()
                if message.received_at or message.created_at
                else None
            ),
        }
        for agent_id in agent_ids:
            broadcast_inbox_updated(str(agent_id[0]), inbox_payload)

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
    timestamp = message.received_at or message.sent_at or _now()
    conversation.last_message_at = timestamp
    conversation.updated_at = timestamp
    db.flush()
    return conversation, message
