"""Conversation status helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models.crm.enums import ConversationStatus
from app.schemas.crm.conversation import ConversationUpdate
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.audit import log_conversation_action
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
        check = validate_transition(conversation.status, status_enum)
        if not check.allowed:
            return UpdateStatusResult(kind="invalid_transition", detail=check.reason)
        conversation_service.Conversations.update(
            db,
            conversation_id,
            ConversationUpdate(status=status_enum),
        )
        log_conversation_action(
            db,
            action="update_status",
            conversation_id=conversation_id,
            actor_id=actor_id,
            metadata={"status": status_enum.value},
        )
        return UpdateStatusResult(kind="updated")
    except ValueError:
        return UpdateStatusResult(kind="invalid_status")
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            return UpdateStatusResult(kind="not_found")
        return UpdateStatusResult(kind="invalid_status")
