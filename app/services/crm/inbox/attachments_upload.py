"""Attachment upload helpers for CRM inbox."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.services.common import coerce_uuid
from app.services.crm.inbox.attachments_processing import (
    prepare_uploads_async,
    save_uploads,
)
from app.services.crm.inbox.permissions import can_upload_attachments


async def save_conversation_attachments(
    db: Session,
    *,
    conversation_id: str,
    files,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> list[dict]:
    if (roles is not None or scopes is not None) and not can_upload_attachments(roles, scopes):
        raise PermissionError("Not authorized to upload attachments")
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
