"""CRM inbox conversation/list/detail partial routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType
from app.services import crm as crm_service
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm.inbox.agents import get_current_agent_id
from app.services.crm.inbox.formatting import (
    filter_messages_for_user,
    format_contact_for_template,
    format_conversation_for_template,
    format_message_for_template,
)

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")


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


def _load_crm_agent_team_options(db: Session) -> dict:
    """Get agents and teams for assignment dropdowns (uses service layer)."""
    return crm_service.get_agent_team_options(db)


@router.get("/inbox/conversations", response_class=HTMLResponse)
async def inbox_conversations_partial(
    request: Request,
    db: Session = Depends(get_db),
    channel: str | None = None,
    status: str | None = None,
    outbox_status: str | None = None,
    search: str | None = None,
    assignment: str | None = None,
    target_id: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    page: int | None = None,
):
    """Partial template for conversation list (HTMX)."""
    from app.services.crm.inbox.page_context import build_inbox_conversations_partial_context
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    assigned_person_id = current_user.get("person_id")
    template_name, context = await build_inbox_conversations_partial_context(
        db,
        channel=channel,
        status=status,
        outbox_status=outbox_status,
        search=search,
        assignment=assignment,
        assigned_person_id=assigned_person_id,
        target_id=target_id,
        offset=offset,
        limit=limit,
        page=page,
    )
    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            **context,
        },
    )


@router.get("/inbox/conversation/{conversation_id}", response_class=HTMLResponse)
async def inbox_conversation_detail(
    request: Request,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """Partial template for conversation thread (HTMX)."""
    from app.services.crm.inbox.thread import load_conversation_thread
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    thread = load_conversation_thread(
        db,
        conversation_id,
        actor_person_id=current_user.get("person_id"),
        mark_read=True,
    )
    if thread.kind != "success":
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Conversation not found</div>")
    if not thread.conversation:
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Conversation not found</div>")

    conversation = format_conversation_for_template(thread.conversation, db, include_inbox_label=True)
    messages = [format_message_for_template(m, db) for m in (thread.messages or [])]
    current_roles = _get_current_roles(request)
    current_agent_id = get_current_agent_id(db, (current_user or {}).get("person_id"))
    messages = filter_messages_for_user(
        messages,
        current_user.get("person_id"),
        current_roles,
    )
    from app.logic import private_note_logic
    from app.services.crm.inbox.agents import list_active_agents_for_mentions
    from app.services.crm.inbox.templates import message_templates

    templates_list = message_templates.list(
        db,
        channel_type=None,
        is_active=True,
        limit=200,
        offset=0,
    )
    mention_agents = list_active_agents_for_mentions(db)

    return templates.TemplateResponse(
        "admin/crm/_message_thread.html",
        {
            "request": request,
            "conversation": conversation,
            "messages": messages,
            "current_user": current_user,
            "current_agent_id": current_agent_id,
            "current_roles": current_roles,
            "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
            "message_templates": templates_list,
            "mention_agents": mention_agents,
        },
    )


@router.get("/inbox/attachment/{message_id}/{attachment_index}")
def inbox_attachment(
    message_id: str,
    attachment_index: int,
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.attachments import fetch_inbox_attachment

    result = fetch_inbox_attachment(db, message_id, attachment_index)
    if result.kind == "redirect" and result.redirect_url:
        return RedirectResponse(result.redirect_url)
    if result.kind == "content" and result.content is not None:
        return Response(
            content=result.content,
            media_type=result.content_type or "application/octet-stream",
            headers={"Content-Disposition": "inline"},
        )
    return Response(status_code=404)


@router.get("/inbox/contact/{contact_id}", response_class=HTMLResponse)
async def inbox_contact_detail(
    request: Request,
    contact_id: str,
    conversation_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Partial template for contact details sidebar (HTMX)."""
    try:
        contact_service.Contacts.get(db, contact_id)
        contact = contact_service.get_person_with_relationships(db, contact_id)
    except Exception:
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Contact not found</div>")

    if not contact:
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Contact not found</div>")

    contact_details = format_contact_for_template(contact, db)
    private_notes = []
    notes_query = (
        db.query(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.person_id == coerce_uuid(contact_id))
        .filter(Message.channel_type == ChannelType.note)
        .order_by(
            func.coalesce(
                Message.received_at,
                Message.sent_at,
                Message.created_at,
            ).desc()
        )
        .limit(10)
        .all()
    )
    for note in notes_query:
        payload = format_message_for_template(note, db)
        if payload.get("is_private_note"):
            private_notes.append(payload)
        if len(private_notes) >= 5:
            break
    assignment_options = _load_crm_agent_team_options(db)
    from app.logic import private_note_logic

    return templates.TemplateResponse(
        "admin/crm/_contact_details.html",
        {
            "request": request,
            "contact": contact_details,
            "conversation_id": conversation_id,
            "agents": assignment_options["agents"],
            "teams": assignment_options["teams"],
            "agent_labels": assignment_options["agent_labels"],
            "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
            "private_notes": private_notes,
        },
    )
