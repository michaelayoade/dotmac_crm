"""CRM inbox conversation status and resolve-gate routes."""

import json
from datetime import datetime
from html import escape as html_escape

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.services.crm.inbox.csat import get_conversation_csat_event
from app.services.crm.inbox.formatting import (
    filter_messages_for_user,
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


def _get_current_scopes(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        scopes = auth.get("scopes") or []
        if isinstance(scopes, list):
            return [str(scope) for scope in scopes]
    return []


def _render_thread_or_error(
    request: Request,
    db: Session,
    conversation_id: str,
    current_user: dict,
) -> HTMLResponse:
    """Shared helper: load conversation thread and return rendered template."""
    from app.services.crm.inbox.thread import load_conversation_thread

    thread = load_conversation_thread(
        db,
        conversation_id,
        actor_person_id=current_user.get("person_id"),
        mark_read=False,
    )
    if thread.kind != "success" or not thread.conversation:
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Conversation not found</div>")

    conversation = format_conversation_for_template(thread.conversation, db, include_inbox_label=True)
    messages = [format_message_for_template(m, db) for m in (thread.messages or [])]
    csat_event = get_conversation_csat_event(db, conversation_id=conversation_id)
    if csat_event and csat_event.timestamp is not None:
        messages.append(
            {
                "id": f"csat-{csat_event.id}",
                "direction": "system",
                "timestamp": csat_event.timestamp,
                "is_private_note": False,
                "is_csat": True,
                "sender": {"name": "CSAT", "initials": "CS"},
                "content": "Customer satisfaction submitted",
                "csat": {
                    "survey_name": csat_event.survey_name,
                    "rating": csat_event.rating,
                    "feedback": csat_event.feedback,
                },
            }
        )
    messages.sort(key=lambda msg: msg["timestamp"].isoformat() if isinstance(msg.get("timestamp"), datetime) else "")
    current_roles = _get_current_roles(request)
    messages = filter_messages_for_user(
        messages,
        current_user.get("person_id"),
        current_roles,
    )
    from app.logic import private_note_logic

    return templates.TemplateResponse(
        "admin/crm/_message_thread.html",
        {
            "request": request,
            "conversation": conversation,
            "messages": messages,
            "current_user": current_user,
            "current_roles": current_roles,
            "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
        },
    )


@router.post("/inbox/conversation/{conversation_id}/status", response_class=HTMLResponse)
async def update_conversation_status(
    request: Request,
    conversation_id: str,
    new_status: str = Query(...),
    db: Session = Depends(get_db),
):
    """Update conversation status.

    When resolving via the message-thread target, an interstitial gate is
    shown if the conversation's person has no active Lead.
    """
    from app.services.crm.inbox.conversation_status import update_conversation_status
    from app.web.admin import get_current_user
    from app.web.admin.crm_inbox_conversations import inbox_conversations_partial

    current_user = get_current_user(request)
    actor_id = (current_user or {}).get("person_id")

    if new_status == "resolved" and request.headers.get("HX-Target") == "message-thread":
        from app.services.crm.inbox.resolve_gate import check_resolve_gate

        gate = check_resolve_gate(db, conversation_id)
        if gate.kind == "needs_gate":
            return templates.TemplateResponse(
                "admin/crm/_resolve_gate.html",
                {
                    "request": request,
                    "conversation_id": conversation_id,
                    "csrf_token": get_csrf_token(request),
                },
            )

    result = update_conversation_status(
        db,
        conversation_id=conversation_id,
        new_status=new_status,
        actor_id=actor_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.kind == "forbidden":
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Forbidden</div>",
            status_code=403,
        )

    if request.headers.get("HX-Target") == "message-thread":
        return _render_thread_or_error(request, db, conversation_id, current_user)

    return await inbox_conversations_partial(request, db)


@router.post("/inbox/conversation/{conversation_id}/priority", response_class=HTMLResponse)
async def update_conversation_priority_route(
    request: Request,
    conversation_id: str,
    priority: str = Query(...),
    db: Session = Depends(get_db),
):
    """Update conversation priority."""
    from app.services.crm.inbox.conversation_status import update_conversation_priority
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    result = update_conversation_priority(
        db,
        conversation_id=conversation_id,
        priority=priority,
        actor_id=(current_user or {}).get("person_id"),
    )
    if result.kind == "invalid_priority":
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Invalid priority</div>", status_code=400)
    if result.kind == "not_found":
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Conversation not found</div>", status_code=404)
    return _render_thread_or_error(request, db, conversation_id, current_user)


@router.post("/inbox/conversation/{conversation_id}/mute", response_class=HTMLResponse)
async def toggle_conversation_mute_route(
    request: Request,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """Toggle mute on a conversation."""
    from app.services.crm.inbox.conversation_status import toggle_conversation_mute
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    result = toggle_conversation_mute(
        db,
        conversation_id=conversation_id,
        actor_id=(current_user or {}).get("person_id"),
    )
    if result.kind == "not_found":
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Conversation not found</div>", status_code=404)
    return _render_thread_or_error(request, db, conversation_id, current_user)


@router.post("/inbox/conversation/{conversation_id}/transcript", response_class=HTMLResponse)
async def send_conversation_transcript(
    request: Request,
    conversation_id: str,
    to_email: str = Form(...),
    db: Session = Depends(get_db),
):
    """Send conversation transcript via email."""
    import re

    from app.services.crm.inbox.transcript import send_conversation_transcript as send_transcript
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    if not email_pattern.match(to_email.strip()):
        headers = {"HX-Trigger": json.dumps({"showToast": {"message": "Invalid email address", "type": "error"}})}
        return HTMLResponse("", headers=headers)

    success, error = send_transcript(
        db,
        conversation_id=conversation_id,
        to_email=to_email.strip(),
        actor_id=(current_user or {}).get("person_id"),
    )
    if not success:
        msg = error or "Failed to send transcript"
        headers = {"HX-Trigger": json.dumps({"showToast": {"message": msg, "type": "error"}})}
        return HTMLResponse("", headers=headers)

    headers = {"HX-Trigger": json.dumps({"showToast": {"message": "Transcript sent!", "type": "success"}})}
    return HTMLResponse("", headers=headers)


@router.post("/inbox/conversation/{conversation_id}/resolve-with-lead", response_class=HTMLResponse)
async def inbox_resolve_with_lead(
    request: Request,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """Create a Lead for the conversation contact, then resolve."""
    from app.services.crm.inbox.resolve_gate import resolve_with_lead
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    actor_id = (current_user or {}).get("person_id")
    outcome = resolve_with_lead(
        db,
        conversation_id=conversation_id,
        actor_id=actor_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if outcome == "forbidden":
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    if outcome == "not_found":
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Conversation not found</div>", status_code=404)
    return _render_thread_or_error(request, db, conversation_id, current_user)


@router.post("/inbox/conversation/{conversation_id}/resolve-without-lead", response_class=HTMLResponse)
async def inbox_resolve_without_lead(
    request: Request,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """Resolve the conversation without creating a lead."""
    from app.services.crm.inbox.resolve_gate import resolve_without_lead
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    actor_id = (current_user or {}).get("person_id")
    outcome = resolve_without_lead(
        db,
        conversation_id=conversation_id,
        actor_id=actor_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if outcome == "forbidden":
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    if outcome == "not_found":
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Conversation not found</div>", status_code=404)
    return _render_thread_or_error(request, db, conversation_id, current_user)


@router.post("/inbox/conversation/{conversation_id}/link-and-resolve", response_class=HTMLResponse)
async def inbox_link_and_resolve(
    request: Request,
    conversation_id: str,
    person_id: str = Form(...),
    db: Session = Depends(get_db),
):
    """Link conversation to an existing contact (merge), then resolve."""
    from app.services.crm.inbox.conversation_actions import resolve_conversation
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    merged_by_id = (current_user or {}).get("person_id")
    result = resolve_conversation(
        db,
        conversation_id=conversation_id,
        person_id=person_id,
        channel_type=None,
        channel_address=None,
        merged_by_id=merged_by_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
        also_resolve=True,
    )
    if result.kind == "forbidden":
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    if result.kind == "not_found":
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Conversation not found</div>", status_code=404)
    if result.kind == "error":
        detail = html_escape(result.error_detail or "Unknown error")
        return HTMLResponse(f"<div class='p-6 text-center text-slate-500'>Error: {detail}</div>", status_code=400)
    return _render_thread_or_error(request, db, conversation_id, current_user)


@router.get("/inbox/templates/search")
async def search_inbox_templates(
    request: Request,
    q: str = Query(""),
    db: Session = Depends(get_db),
):
    """Search message templates for shortcode autocomplete."""
    from app.services.crm.inbox.templates import message_templates

    query = q.strip()
    if not query:
        return JSONResponse([])

    results = message_templates.search(db, query, limit=10)
    return JSONResponse(
        [
            {
                "id": str(t.id),
                "name": t.name,
                "body": t.body or "",
                "channel_type": t.channel_type.value if t.channel_type else None,
            }
            for t in results
        ]
    )
