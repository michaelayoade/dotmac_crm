"""CRM inbox core action routes (assignment and resolve)."""

from typing import cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger
from app.models.person import Person
from app.services import crm as crm_service
from app.services.crm import contact as contact_service
from app.services.crm.inbox.formatting import format_contact_for_template

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)


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


def _load_crm_agent_team_options(db: Session) -> dict:
    return crm_service.get_agent_team_options(db)


@router.post("/inbox/conversation/{conversation_id}/assignment", response_class=HTMLResponse)
def inbox_conversation_assignment(
    request: Request,
    conversation_id: str,
    agent_id: str | None = Form(None),
    team_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.conversation_actions import assign_conversation
    from app.web.admin import get_current_user

    conversation_result = assign_conversation(
        db,
        conversation_id=conversation_id,
        agent_id=agent_id,
        team_id=team_id,
        assigned_by_id=(get_current_user(request).get("person_id") or "").strip() or None,
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
        return HTMLResponse(
            "<div class='p-4 text-sm text-red-500'>Invalid agent or team selection.</div>",
            status_code=200,
        )
    if conversation_result.kind == "error":
        logger.exception("Failed to assign conversation.")
        if request.headers.get("HX-Request"):
            conversation = conversation_result.conversation
            contact = (
                contact_service.get_person_with_relationships(db, str(conversation.contact_id))
                if conversation
                else None
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
    from app.web.admin import get_current_user

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
