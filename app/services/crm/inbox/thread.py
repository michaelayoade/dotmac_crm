"""Conversation thread helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, Message
from app.services.crm.conversation import mark_conversation_read
from app.services.crm.conversations import service as conversation_service


@dataclass(frozen=True)
class ThreadLoadResult:
    kind: Literal["not_found", "success"]
    conversation: Conversation | None = None
    messages: list[Message] | None = None
    last_seen_at: datetime | None = None


def load_conversation_thread(
    db: Session,
    conversation_id: str,
    *,
    actor_person_id: str | None,
    mark_read: bool = True,
) -> ThreadLoadResult:
    try:
        conversation = conversation_service.Conversations.get(db, conversation_id)
    except Exception:
        return ThreadLoadResult(kind="not_found")

    messages_raw = conversation_service.Messages.list(
        db=db,
        conversation_id=conversation_id,
        channel_type=None,
        direction=None,
        status=None,
        order_by="created_at",
        order_dir="desc",
        limit=100,
        offset=0,
    )
    messages_raw = list(reversed(messages_raw))
    last_seen_at = None
    if messages_raw:
        last_seen_at = max(
            [
                msg.received_at or msg.sent_at or msg.created_at
                for msg in messages_raw
                if msg.received_at or msg.sent_at or msg.created_at
            ],
            default=None,
        )

    if mark_read:
        mark_conversation_read(db, conversation_id, actor_person_id, last_seen_at)

    return ThreadLoadResult(
        kind="success",
        conversation=conversation,
        messages=messages_raw,
        last_seen_at=last_seen_at,
    )
