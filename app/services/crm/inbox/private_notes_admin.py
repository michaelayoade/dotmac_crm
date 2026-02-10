"""Private note helpers for CRM inbox (admin)."""

from __future__ import annotations

from typing import cast

from sqlalchemy.orm import Session

from app.logic.private_note_logic import Visibility
from app.services.crm import conversation as conversation_service
from app.services.crm import private_notes as private_notes_service
from app.services.crm.inbox.attachments_processing import apply_message_attachments
from app.services.crm.inbox.audit import log_note_action
from app.services.crm.inbox.permissions import can_manage_private_notes


def create_private_note(
    db: Session,
    *,
    conversation_id: str,
    author_id: str | None,
    body: str,
    requested_visibility: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
):
    visibility: Visibility | None = None
    if requested_visibility in {"author", "team", "admins"}:
        visibility = cast(Visibility, requested_visibility)
    if (roles is not None or scopes is not None) and not can_manage_private_notes(roles, scopes):
        raise PermissionError("Not authorized to create private notes")
    conversation_service.Conversations.get(db, conversation_id)
    return private_notes_service.create(
        db=db,
        conversation_id=conversation_id,
        author_id=author_id,
        body=body,
        requested_visibility=visibility,
    )


def create_private_note_with_attachments(
    db: Session,
    *,
    conversation_id: str,
    author_id: str | None,
    body: str,
    requested_visibility: str | None,
    attachments: list[dict] | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
):
    note = create_private_note(
        db,
        conversation_id=conversation_id,
        author_id=author_id,
        body=body,
        requested_visibility=requested_visibility,
        roles=roles,
        scopes=scopes,
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
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
):
    if (roles is not None or scopes is not None) and not can_manage_private_notes(roles, scopes):
        raise PermissionError("Not authorized to delete private notes")
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
