"""Attachment processing helpers for CRM inbox."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from app.models.crm.conversation import Message
from app.schemas.crm.conversation import MessageAttachmentCreate
from app.services.crm.conversations import message_attachments as message_attachments_service
from app.services.crm.inbox.errors import InboxValidationError


async def prepare_uploads_async(files) -> list[dict]:
    return await message_attachments_service.prepare(files)


def save_uploads(prepared: list[dict]) -> list[dict]:
    return message_attachments_service.save(prepared)


def _validate_attachment_payload(item: dict) -> None:
    if not item.get("stored_name"):
        raise InboxValidationError("attachment_stored_name_missing", "Attachment storage key missing")
    if not item.get("file_name"):
        raise InboxValidationError("attachment_file_name_missing", "Attachment filename required")
    if not item.get("mime_type"):
        raise InboxValidationError("attachment_mime_type_missing", "Attachment mime type required")
    if not item.get("file_size"):
        raise InboxValidationError("attachment_file_size_missing", "Attachment file size required")


def apply_message_attachments(
    db: Session,
    message: Message,
    attachments: Iterable[dict] | None,
) -> None:
    if not attachments:
        return
    for item in attachments:
        if not isinstance(item, dict):
            continue
        _validate_attachment_payload(item)
        message_attachments_service.create(
            db,
            MessageAttachmentCreate(
                message_id=message.id,
                file_name=item.get("file_name"),
                mime_type=item.get("mime_type"),
                file_size=item.get("file_size"),
                external_url=item.get("url"),
                metadata_={"stored_name": item.get("stored_name")},
            ),
        )
