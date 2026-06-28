"""Inbox collaboration API — private notes + message attachments.

The deferred follow-up from the inbox-actions work: thin wrappers over the
private-note service so a mobile/external agent can read, add, and remove
internal notes on a conversation, and list a message's attachments. Mounted
under the CRM router (require_user_auth).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.crm.conversation import Message, MessageAttachment
from app.models.crm.enums import ChannelType
from app.services.common import coerce_uuid
from app.services.crm.conversations.private_notes import delete_private_note, send_private_note

router = APIRouter(prefix="/crm/conversations", tags=["crm-inbox-collab"])


def _person_id(auth) -> str | None:
    return str(auth["person_id"]) if auth and auth.get("person_id") else None


def _note_out(message: Message) -> dict:
    metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
    return {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "body": message.body,
        "author_id": str(message.author_id) if message.author_id else None,
        "visibility": metadata.get("visibility"),
        "created_at": message.created_at,
    }


class PrivateNoteCreate(BaseModel):
    body: str
    visibility: Literal["author", "team", "admins"] | None = None


@router.get("/{conversation_id}/notes")
def list_private_notes(conversation_id: str, db: Session = Depends(get_db)):
    notes = (
        db.query(Message)
        .filter(Message.conversation_id == coerce_uuid(conversation_id))
        .filter(Message.channel_type == ChannelType.note)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [_note_out(n) for n in notes]


@router.post("/{conversation_id}/notes", status_code=201)
def create_private_note(
    conversation_id: str,
    payload: PrivateNoteCreate,
    db: Session = Depends(get_db),
    auth=Depends(get_current_user),
):
    note = send_private_note(db, conversation_id, _person_id(auth), payload.body, payload.visibility)
    return _note_out(note)


@router.delete("/{conversation_id}/notes/{note_id}", status_code=204)
def remove_private_note(
    conversation_id: str, note_id: str, db: Session = Depends(get_db), auth=Depends(get_current_user)
):
    delete_private_note(db, conversation_id, note_id, _person_id(auth))


@router.get("/messages/{message_id}/attachments")
def list_message_attachments(message_id: str, db: Session = Depends(get_db)):
    if db.get(Message, coerce_uuid(message_id)) is None:
        raise HTTPException(status_code=404, detail="Message not found")
    attachments = db.query(MessageAttachment).filter(MessageAttachment.message_id == coerce_uuid(message_id)).all()
    return [
        {
            "id": str(a.id),
            "file_name": a.file_name,
            "mime_type": a.mime_type,
            "file_size": a.file_size,
            "external_url": a.external_url,
        }
        for a in attachments
    ]
