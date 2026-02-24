"""Conversation status helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationPriority, ConversationStatus
from app.schemas.crm.conversation import ConversationUpdate
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox.audit import log_conversation_action
from app.services.crm.inbox.csat import queue_for_resolved_conversation
from app.services.crm.inbox.permissions import can_update_conversation_status
from app.services.crm.inbox.status_flow import validate_transition


@dataclass(frozen=True)
class UpdateStatusResult:
    kind: Literal[
        "forbidden",
        "invalid_status",
        "invalid_transition",
        "not_found",
        "updated",
    ]
    detail: str | None = None


def update_conversation_status(
    db: Session,
    *,
    conversation_id: str,
    new_status: str,
    actor_id: str | None = None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> UpdateStatusResult:
    try:
        if (roles is not None or scopes is not None) and not can_update_conversation_status(roles, scopes):
            return UpdateStatusResult(kind="forbidden", detail="Not authorized")
        status_enum = ConversationStatus(new_status)
        conversation = conversation_service.Conversations.get(db, conversation_id)
        previous_status = conversation.status
        check = validate_transition(conversation.status, status_enum)
        if not check.allowed:
            return UpdateStatusResult(kind="invalid_transition", detail=check.reason)
        conversation_service.Conversations.update(
            db,
            conversation_id,
            ConversationUpdate(status=status_enum),
        )
        inbox_cache.invalidate_inbox_list()
        log_conversation_action(
            db,
            action="update_status",
            conversation_id=conversation_id,
            actor_id=actor_id,
            metadata={"status": status_enum.value},
        )
        if (
            db is not None
            and status_enum == ConversationStatus.resolved
            and previous_status != ConversationStatus.resolved
        ):
            # CSAT enqueue is best-effort and should never block resolve operations.
            queue_for_resolved_conversation(
                db,
                conversation_id=conversation_id,
                author_id=actor_id,
            )
        return UpdateStatusResult(kind="updated")
    except ValueError:
        return UpdateStatusResult(kind="invalid_status")
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            return UpdateStatusResult(kind="not_found")
        raise


@dataclass(frozen=True)
class UpdatePriorityResult:
    kind: Literal["not_found", "invalid_priority", "updated"]
    detail: str | None = None


def update_conversation_priority(
    db: Session,
    *,
    conversation_id: str,
    priority: str,
    actor_id: str | None = None,
) -> UpdatePriorityResult:
    """Update conversation priority via the service layer."""
    try:
        priority_enum = ConversationPriority(priority)
    except ValueError:
        return UpdatePriorityResult(kind="invalid_priority", detail=f"Invalid priority: {priority}")

    conv = db.get(Conversation, coerce_uuid(conversation_id))
    if not conv:
        return UpdatePriorityResult(kind="not_found")

    conv.priority = priority_enum
    db.commit()
    inbox_cache.invalidate_inbox_list()
    log_conversation_action(
        db,
        conversation_id=conversation_id,
        action="priority_changed",
        actor_id=actor_id,
        metadata={"priority": priority},
    )
    return UpdatePriorityResult(kind="updated")


@dataclass(frozen=True)
class ToggleMuteResult:
    kind: Literal["not_found", "updated"]
    is_muted: bool = False


def toggle_conversation_mute(
    db: Session,
    *,
    conversation_id: str,
    actor_id: str | None = None,
) -> ToggleMuteResult:
    """Toggle mute on a conversation via the service layer."""
    conv = db.get(Conversation, coerce_uuid(conversation_id))
    if not conv:
        return ToggleMuteResult(kind="not_found")

    conv.is_muted = not conv.is_muted
    db.commit()
    inbox_cache.invalidate_inbox_list()
    log_conversation_action(
        db,
        conversation_id=conversation_id,
        action="mute_toggled",
        actor_id=actor_id,
        metadata={"is_muted": conv.is_muted},
    )
    return ToggleMuteResult(kind="updated", is_muted=conv.is_muted)
