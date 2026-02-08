"""Private note helpers for CRM inbox (admin)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.crm import private_notes as private_notes_service
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.attachments_processing import apply_message_attachments
from app.services.crm.inbox.audit import log_note_action


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
    log_note_action(
        db,
        action="create_private_note",
        note_id=str(note.id),
        actor_id=author_id,
        metadata={"conversation_id": conversation_id},
    )
    if attachments:
        apply_message_attachments(db, note, attachments)
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
    log_note_action(
        db,
        action="delete_private_note",
        note_id=note_id,
        actor_id=actor_id,
        metadata={"conversation_id": conversation_id},
    )
