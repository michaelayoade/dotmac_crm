"""Conversation status helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models.crm.enums import ConversationStatus
from app.schemas.crm.conversation import ConversationUpdate
from app.services.crm import conversation as conversation_service


@dataclass(frozen=True)
class UpdateStatusResult:
    kind: Literal["invalid_status", "updated"]


def update_conversation_status(
    db: Session,
    *,
    conversation_id: str,
    new_status: str,
) -> UpdateStatusResult:
    try:
        status_enum = ConversationStatus(new_status)
        conversation_service.Conversations.update(
            db,
            conversation_id,
            ConversationUpdate(status=status_enum),
        )
        return UpdateStatusResult(kind="updated")
    except (ValueError, Exception):
        return UpdateStatusResult(kind="invalid_status")
