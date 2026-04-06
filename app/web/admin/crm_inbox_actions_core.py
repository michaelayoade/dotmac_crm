"""CRM inbox core action routes (assignment and resolve)."""

import html
import json
from typing import cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm.inbox.formatting import format_contact_for_template
from app.web.admin.crm_support import _get_current_roles, _get_current_scopes, _load_crm_agent_team_options
from app.web.templates import Jinja2Templates

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/inbox/conversations/bulk", response_class=HTMLResponse)
def inbox_conversations_bulk_action(
    request: Request,
    conversation_ids: list[str] = Form(default=[]),
    bulk_action: str = Form(...),
    bulk_label: str | None = Form(None),
    current_agent_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.bulk_actions import apply_bulk_action
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request) or {}
    actor_id = (current_user.get("person_id") or "").strip() or None
    result = apply_bulk_action(
        db,
        conversation_ids=conversation_ids,
        action=bulk_action,
        actor_id=actor_id,
        current_agent_id=(current_agent_id or "").strip() or None,
        label=bulk_label,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if request.headers.get("HX-Request"):
        message = (
            result.detail
            if result.kind == "invalid_action"
            else f"Bulk action complete: {result.applied} applied, {result.skipped} skipped, {result.failed} failed"
        )
        trigger = {
            "showToast": {
                "type": "error" if result.kind == "invalid_action" else "success",
                "title": "Bulk action",
                "message": message,
            },
            "inboxBulkApplied": {
                "ok": result.kind == "success",
                "applied": result.applied,
                "skipped": result.skipped,
                "failed": result.failed,
            },
        }
        return Response(status_code=204, headers={"HX-Trigger": json.dumps(trigger)})

    return RedirectResponse(url="/admin/crm/inbox", status_code=303)


@router.post("/inbox/saved-filters", response_class=HTMLResponse)
def inbox_save_filter(
    request: Request,
    name: str = Form(...),
    channel: str | None = Form(None),
    status: str | None = Form(None),
    outbox_status: str | None = Form(None),
    search: str | None = Form(None),
    assignment: str | None = Form(None),
    target_id: str | None = Form(None),
    agent_id: str | None = Form(None),
    assigned_from: str | None = Form(None),
    assigned_to: str | None = Form(None),
    limit: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox import saved_filters as saved_filters_service
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request) or {}
    person_id_raw = (current_user.get("person_id") or "").strip()
    if not person_id_raw:
        return RedirectResponse(url="/admin/crm/inbox", status_code=303)

    saved = saved_filters_service.save_saved_filter(
        db,
        coerce_uuid(person_id_raw),
        name=name,
        params={
            "channel": channel,
            "status": status,
            "outbox_status": outbox_status,
            "search": search,
            "assignment": assignment,
            "target_id": target_id,
            "agent_id": agent_id,
            "assigned_from": assigned_from,
            "assigned_to": assigned_to,
            "limit": limit,
        },
    )
    if saved and saved.get("id"):
        return RedirectResponse(url=f"/admin/crm/inbox?saved_filter_id={saved['id']}", status_code=303)
    return RedirectResponse(url="/admin/crm/inbox", status_code=303)


@router.post("/inbox/saved-filters/{filter_id}/delete", response_class=HTMLResponse)
def inbox_delete_filter(
    request: Request,
    filter_id: str,
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox import saved_filters as saved_filters_service
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request) or {}
    person_id_raw = (current_user.get("person_id") or "").strip()
    if person_id_raw:
        saved_filters_service.delete_saved_filter(db, coerce_uuid(person_id_raw), filter_id)
    return RedirectResponse(url="/admin/crm/inbox", status_code=303)


@router.post("/inbox/conversation/{conversation_id}/assignment", response_class=HTMLResponse)
def inbox_conversation_assignment(
    request: Request,
    conversation_id: str,
    agent_id: str | None = Form(None),
    team_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.conversation_actions import assign_conversation
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request) or {}
    assigned_by_id = (current_user.get("person_id") or "").strip() or None
    conversation_result = assign_conversation(
        db,
        conversation_id=conversation_id,
        agent_id=agent_id,
        team_id=team_id,
        assigned_by_id=assigned_by_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if conversation_result.kind == "forbidden":
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Forbidden</div>",
            status_code=403,
        )
    if conversation_result.kind == "not_found":
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Conversation not found</div>",
            status_code=404,
        )
    if conversation_result.kind == "invalid_input":
        detail = (conversation_result.error_detail or "Invalid agent or team selection.").strip()
        safe_detail = html.escape(detail, quote=True)
        return HTMLResponse(
            f"<div class='p-4 text-sm text-red-500'>{safe_detail}</div>",
            status_code=200,
        )
    if conversation_result.kind == "error":
        db.rollback()
        conversation = conversation_result.conversation
        conversation_status = None
        contact_person_id = (conversation_result.contact_person_id or "").strip() or None
        if conversation is not None:
            try:
                conversation_status = conversation.status.value if conversation.status else None
            except Exception:
                conversation_status = None
            if not contact_person_id:
                try:
                    if conversation.person_id is not None:
                        contact_person_id = str(conversation.person_id)
                except Exception:
                    contact_person_id = None
        logger.warning(
            "crm_inbox_assignment_failed conversation_id=%s agent_id=%s team_id=%s "
            "assigned_by_id=%s status=%s contact_person_id=%s detail=%s",
            conversation_id,
            (agent_id or "").strip() or None,
            (team_id or "").strip() or None,
            assigned_by_id,
            conversation_status,
            contact_person_id,
            (conversation_result.error_detail or "Assignment failed").strip(),
        )
        if request.headers.get("HX-Request"):
            if not contact_person_id and conversation is not None:
                try:
                    if conversation.person_id is not None:
                        contact_person_id = str(conversation.person_id)
                except Exception:
                    contact_person_id = None
            contact = (
                contact_service.get_person_with_relationships(db, contact_person_id) if contact_person_id else None
            )
            if contact:
                contact_details = format_contact_for_template(contact, db)
                assignment_options = _load_crm_agent_team_options(db)
                from app.logic import private_note_logic

                return templates.TemplateResponse(
                    "admin/crm/_contact_details.html",
                    {
                        "request": request,
                        "contact": contact_details,
                        "conversation_id": str(conversation.id) if conversation else None,
                        "agents": assignment_options["agents"],
                        "teams": assignment_options["teams"],
                        "agent_labels": assignment_options["agent_labels"],
                        "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
                        "assignment_error_detail": conversation_result.error_detail or "Assignment failed",
                    },
                )
            return HTMLResponse(
                "<div class='p-6 text-center text-slate-500'>Contact not found</div>",
                status_code=200,
            )
        return RedirectResponse(
            url=f"/admin/crm/inbox?conversation_id={conversation_id}",
            status_code=303,
        )

    contact = cast(Person | None, conversation_result.contact)
    if not contact:
        return RedirectResponse(
            url=f"/admin/crm/inbox?conversation_id={conversation_id}",
            status_code=303,
        )
    conversation = conversation_result.conversation
    if not conversation:
        return RedirectResponse(
            url=f"/admin/crm/inbox?conversation_id={conversation_id}",
            status_code=303,
        )
    contact_details = format_contact_for_template(contact, db)
    assignment_options = _load_crm_agent_team_options(db)
    if request.headers.get("HX-Request"):
        from app.logic import private_note_logic

        return templates.TemplateResponse(
            "admin/crm/_contact_details.html",
            {
                "request": request,
                "contact": contact_details,
                "conversation_id": str(conversation.id),
                "agents": assignment_options["agents"],
                "teams": assignment_options["teams"],
                "agent_labels": assignment_options["agent_labels"],
                "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
            },
        )
    return RedirectResponse(
        url=f"/admin/crm/inbox?conversation_id={conversation_id}",
        status_code=303,
    )


@router.post("/inbox/conversation/{conversation_id}/resolve", response_class=HTMLResponse)
def inbox_conversation_resolve(
    request: Request,
    conversation_id: str,
    person_id: str = Form(...),
    subscriber_id: str | None = Form(None),
    channel_type: str | None = Form(None),
    channel_address: str | None = Form(None),
    db: Session = Depends(get_db),
):
    _ = subscriber_id
    from app.services.crm.inbox.conversation_actions import resolve_conversation
    from app.web.admin._auth_helpers import get_current_user

    result = resolve_conversation(
        db,
        conversation_id=conversation_id,
        person_id=person_id,
        channel_type=channel_type,
        channel_address=channel_address,
        merged_by_id=(get_current_user(request).get("person_id") if get_current_user(request) else None),
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if result.kind == "forbidden":
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Forbidden</div>",
            status_code=403,
        )
    if result.kind == "not_found":
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Conversation not found</div>",
            status_code=404,
        )
    if result.kind == "invalid_channel":
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Invalid channel type</div>",
            status_code=400,
        )
    if result.kind == "error":
        return HTMLResponse(
            f"<div class='p-6 text-center text-slate-500'>Resolve failed: {result.error_detail}</div>",
            status_code=400,
        )

    contact = cast(Person | None, result.contact)
    if not contact:
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Contact not found</div>",
            status_code=404,
        )
    if not result.conversation:
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Conversation not found</div>",
            status_code=404,
        )

    contact_details = format_contact_for_template(contact, db)
    assignment_options = _load_crm_agent_team_options(db)
    from app.logic import private_note_logic

    return templates.TemplateResponse(
        "admin/crm/_contact_details.html",
        {
            "request": request,
            "contact": contact_details,
            "conversation_id": str(result.conversation.id),
            "agents": assignment_options["agents"],
            "teams": assignment_options["teams"],
            "agent_labels": assignment_options["agent_labels"],
            "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
        },
    )
