"""Attachment upload helpers for CRM inbox."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.services.common import coerce_uuid
from app.services.crm.conversations import message_attachments as message_attachment_service


async def save_conversation_attachments(
    db: Session,
    *,
    conversation_id: str,
    files,
) -> list[dict]:
    try:
        conversation_uuid = coerce_uuid(conversation_id)
    except Exception:
        raise ValueError("Conversation not found")
    conversation = db.get(Conversation, conversation_uuid)
    if not conversation:
        raise ValueError("Conversation not found")
    prepared = await message_attachment_service.prepare(files)
    if not prepared:
        raise ValueError("No attachments provided")
    return message_attachment_service.save(prepared)
