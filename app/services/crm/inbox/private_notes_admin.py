"""Private note helpers for CRM inbox (admin)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.crm.conversation import MessageAttachmentCreate
from app.services.crm import private_notes as private_notes_service
from app.services.crm import conversation as conversation_service
from app.services.crm.conversations import message_attachments as message_attachments_service


def create_private_note(
    db: Session,
    *,
    conversation_id: str,
    author_id: str | None,
    body: str,
    requested_visibility: str | None,
):
    conversation_service.Conversations.get(db, conversation_id)
    return private_notes_service.create(
        db=db,
        conversation_id=conversation_id,
        author_id=author_id,
        body=body,
        requested_visibility=requested_visibility,
    )


def create_private_note_with_attachments(
    db: Session,
    *,
    conversation_id: str,
    author_id: str | None,
    body: str,
    requested_visibility: str | None,
    attachments: list[dict] | None,
):
    note = create_private_note(
        db,
        conversation_id=conversation_id,
        author_id=author_id,
        body=body,
        requested_visibility=requested_visibility,
    )
    if attachments:
        for item in attachments:
            if not isinstance(item, dict):
                continue
            message_attachments_service.create(
                db,
                MessageAttachmentCreate(
                    message_id=note.id,
                    file_name=item.get("file_name"),
                    mime_type=item.get("mime_type"),
                    file_size=item.get("file_size"),
                    external_url=item.get("url"),
                    metadata_={"stored_name": item.get("stored_name")},
                ),
            )
    return note


def delete_private_note(
    db: Session,
    *,
    conversation_id: str,
    note_id: str,
    actor_id: str | None,
):
    private_notes_service.delete(
        db=db,
        conversation_id=conversation_id,
        note_id=note_id,
        actor_id=actor_id,
    )
