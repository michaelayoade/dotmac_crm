from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.logic import private_note_logic
from app.logic.private_note_logic import LogicService, PrivateNoteContext, Visibility
from typing import cast
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection, MessageStatus
from app.models.rbac import PersonRole, Role
from app.schemas.crm.conversation import MessageCreate
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service

_logic_service = LogicService()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _author_is_admin(db: Session, author_id: str | None) -> bool:
    if not author_id:
        return False
    role = (
        db.query(Role)
        .filter(Role.name == "admin")
        .filter(Role.is_active.is_(True))
        .first()
    )
    if not role:
        return False
    link = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == coerce_uuid(author_id))
        .filter(PersonRole.role_id == role.id)
        .first()
    )
    return link is not None


def _is_system_conversation(conversation: Conversation) -> bool:
    metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
    if not metadata:
        return False
    if metadata.get("is_system") or metadata.get("system") or metadata.get("system_conversation"):
        return True
    conv_type = metadata.get("type")
    return isinstance(conv_type, str) and conv_type.lower() == "system"


def _normalize_visibility_fallback(value: str | None) -> Visibility:
    if value in ("author", "team", "admins"):
        return cast(Visibility, value)
    return "team"


def send_private_note(
    db: Session,
    conversation_id: str,
    author_id: str | None,
    body: str | None,
    requested_visibility: Visibility | None,
) -> Message:
    """Create an internal-only private note message for a conversation."""
    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    author_is_admin = _author_is_admin(db, author_id)
    use_logic_service = private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE
    if use_logic_service:
        ctx = PrivateNoteContext(
            body=body,
            is_system_conversation=_is_system_conversation(conversation),
            author_is_admin=author_is_admin,
            requested_visibility=requested_visibility,
        )
        decision = _logic_service.decide_create_note(ctx)
        if decision.status == "deny":
            raise HTTPException(status_code=400, detail=decision.reason or "Private note denied")
        visibility = decision.visibility or "team"
    else:
        visibility = _normalize_visibility_fallback(requested_visibility)

    # Preserve ordering for internal notes by setting an explicit timestamp.
    received_at = _now() if use_logic_service else None
    sent_at = None if use_logic_service else _now()
    status = MessageStatus.received

    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            channel_type=ChannelType.note,
            direction=MessageDirection.internal,
            status=status,
            body=body,
            author_id=coerce_uuid(author_id) if author_id else None,
            received_at=received_at,
            sent_at=sent_at,
            metadata_={
                "type": "private_note",
                "visibility": visibility,
            },
        ),
    )
    return message
