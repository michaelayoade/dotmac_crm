"""CRM inbox private-note and attachment routes."""

from typing import Literal

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, Response

from app.db import SessionLocal
from app.services.crm.inbox.formatting import filter_messages_for_user, format_message_for_template

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")


class PrivateNoteCreate(BaseModel):
    """Payload for creating a private note in an inbox conversation."""

    body: str
    requested_visibility: Literal["author", "team", "admins"] | None = None


class PrivateNoteRequest(BaseModel):
    """Payload for creating a private note via JSON."""

    body: str
    visibility: Literal["author", "team", "admins"] | None = None
    attachments: list[dict] | None = None
    mentions: list[str] | None = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_current_roles(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        roles = auth.get("roles") or []
        if isinstance(roles, list):
            return [str(role) for role in roles]
    return []


def _get_current_scopes(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        scopes = auth.get("scopes") or []
        if isinstance(scopes, list):
            return [str(scope) for scope in scopes]
    return []


@router.post("/inbox/conversation/{conversation_id}/note")
def create_private_note(
    request: Request,
    conversation_id: str,
    payload: PrivateNoteCreate,
    db: Session = Depends(get_db),
):
    """Create an internal-only private note for a conversation."""
    from fastapi import HTTPException

    from app.logic import private_note_logic
    from app.services.crm.inbox.private_notes_admin import create_private_note
    from app.web.admin import get_current_user

    if not private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    try:
        current_user = get_current_user(request) or {}
        author_id = current_user.get("person_id")
        note = create_private_note(
            db,
            conversation_id=conversation_id,
            author_id=author_id,
            body=payload.body,
            requested_visibility=payload.requested_visibility,
            roles=_get_current_roles(request),
            scopes=_get_current_scopes(request),
        )
    except PermissionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    metadata = note.metadata_ if isinstance(note.metadata_, dict) else {}
    return JSONResponse(
        {
            "id": str(note.id),
            "conversation_id": str(note.conversation_id),
            "author_id": str(note.author_id) if note.author_id else None,
            "body": note.body,
            "visibility": metadata.get("visibility"),
            "type": metadata.get("type"),
            "created_at": note.created_at.isoformat() if note.created_at else None,
        }
    )


@router.post("/inbox/{conversation_id}/private_note")
def create_private_note_api(
    request: Request,
    conversation_id: str,
    payload: PrivateNoteRequest,
    db: Session = Depends(get_db),
):
    """Create a private note via JSON and return note metadata."""
    from fastapi import HTTPException

    from app.services.crm.inbox.private_notes_admin import create_private_note_with_attachments
    from app.web.admin import get_current_user

    if not payload.body or not payload.body.strip():
        return JSONResponse({"detail": "Private note body is empty"}, status_code=400)

    try:
        current_user = get_current_user(request) or {}
        author_id = current_user.get("person_id")
        attachments = payload.attachments or []
        note = create_private_note_with_attachments(
            db,
            conversation_id=conversation_id,
            author_id=author_id,
            body=payload.body,
            requested_visibility=payload.visibility,
            attachments=attachments,
            mentions=payload.mentions,
            roles=_get_current_roles(request),
            scopes=_get_current_scopes(request),
        )
    except PermissionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    message = format_message_for_template(note, db)
    current_roles = _get_current_roles(request)
    visible = filter_messages_for_user([message], author_id, current_roles)
    if not visible:
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    message = visible[0]

    accept = request.headers.get("accept", "")
    if "text/html" in accept or request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            "admin/crm/_private_note_item.html",
            {
                "request": request,
                "msg": message,
            },
        )

    return JSONResponse(
        {
            "id": message["id"],
            "conversation_id": str(note.conversation_id),
            "author_id": message.get("author_id"),
            "body": message["content"],
            "visibility": message.get("visibility"),
            "type": "private_note",
            "received_at": note.received_at.isoformat() if note.received_at else None,
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "timestamp": message["timestamp"].isoformat() if message.get("timestamp") else None,
            "attachments": message.get("attachments") or [],
        }
    )


@router.delete("/inbox/conversation/{conversation_id}/private_note/{note_id}")
def delete_private_note_api(
    request: Request,
    conversation_id: str,
    note_id: str,
    db: Session = Depends(get_db),
):
    """Delete a private note in a conversation."""
    from fastapi import HTTPException

    from app.services.crm.inbox.private_notes_admin import delete_private_note
    from app.web.admin import get_current_user

    current_user = get_current_user(request) or {}
    author_id = current_user.get("person_id")

    try:
        delete_private_note(
            db,
            conversation_id=conversation_id,
            note_id=note_id,
            actor_id=author_id,
            roles=_get_current_roles(request),
            scopes=_get_current_scopes(request),
        )
    except PermissionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return Response(status_code=204)


@router.post("/inbox/conversation/{conversation_id}/attachments")
async def upload_conversation_attachments(
    request: Request,
    conversation_id: str,
    files: UploadFile | list[UploadFile] | tuple[UploadFile, ...] | None = File(None),
    db: Session = Depends(get_db),
):
    """Upload attachments for a conversation message/private note."""
    from app.services.crm.inbox.attachments_upload import save_conversation_attachments

    try:
        saved = await save_conversation_attachments(
            db,
            conversation_id=conversation_id,
            files=files,
            roles=_get_current_roles(request),
            scopes=_get_current_scopes(request),
        )
    except PermissionError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    except ValueError as exc:
        message = str(exc) or "No attachments provided"
        status_code = 404 if "Conversation not found" in message else 400
        return JSONResponse({"detail": message}, status_code=status_code)
    return JSONResponse({"attachments": saved})
