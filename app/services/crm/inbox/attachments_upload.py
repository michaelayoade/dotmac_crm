"""Attachment upload helpers for CRM inbox."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.services.common import coerce_uuid
from app.services.crm.inbox.attachments_processing import (
    prepare_uploads_async,
    save_uploads,
)


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
    prepared = await prepare_uploads_async(files)
    if not prepared:
        raise ValueError("No attachments provided")
    return save_uploads(prepared)
