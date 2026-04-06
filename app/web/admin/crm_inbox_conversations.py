"""CRM inbox conversation/list/detail partial routes."""

from datetime import UTC, datetime, time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.web.admin.crm_support import _get_current_roles
from app.web.templates import Jinja2Templates

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _parse_date_param(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    """Parse a YYYY-MM-DD string into a timezone-aware datetime."""
    if not value:
        return None
    try:
        d = datetime.strptime(value.strip(), "%Y-%m-%d")
        t = time.max if end_of_day else time.min
        return datetime.combine(d.date(), t, tzinfo=UTC)
    except ValueError:
        return None


@router.get("/inbox/summary-counts", response_class=JSONResponse)
async def inbox_summary_counts(
    request: Request,
    db: Session = Depends(get_db),
):
    """Live summary counters for inbox sidebar chips/KPI."""
    from app.services.crm.inbox.queries import get_assignment_counts, get_inbox_stats, get_resolved_today_count
    from app.services.time_preferences import resolve_company_time_prefs
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    assigned_person_id = current_user.get("person_id") if isinstance(current_user, dict) else None
    timezone = resolve_company_time_prefs(db)[0]
    return JSONResponse(
        {
            "assignment_counts": get_assignment_counts(db, assigned_person_id=assigned_person_id),
            "unread": int(get_inbox_stats(db).get("unread", 0)),
            "resolved_today": get_resolved_today_count(db, timezone=timezone),
        }
    )


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
    agent_id: str | None = None,
    assigned_from: str | None = None,
    assigned_to: str | None = None,
    missing: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    page: int | None = None,
):
    """Partial template for conversation list (HTMX)."""
    from app.services.crm.inbox.page_context import build_inbox_conversations_partial_context
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    assigned_person_id = current_user.get("person_id")
    assigned_from_dt = _parse_date_param(assigned_from)
    assigned_to_dt = _parse_date_param(assigned_to, end_of_day=True)
    template_name, context = await build_inbox_conversations_partial_context(
        db,
        channel=channel,
        status=status,
        outbox_status=outbox_status,
        search=search,
        assignment=assignment,
        assigned_person_id=assigned_person_id,
        target_id=target_id,
        filter_agent_id=agent_id,
        assigned_from=assigned_from_dt,
        assigned_to=assigned_to_dt,
        missing=missing,
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
    from app.services.crm.inbox.page_context import build_inbox_conversation_detail_context
    from app.web.admin._auth_helpers import get_current_user

    current_user = get_current_user(request)
    current_roles = _get_current_roles(request)
    detail_context = build_inbox_conversation_detail_context(
        db,
        conversation_id=conversation_id,
        current_user=current_user,
        current_roles=current_roles,
    )
    if not detail_context:
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Conversation not found</div>")
    from app.logic import private_note_logic

    return templates.TemplateResponse(
        "admin/crm/_message_thread.html",
        {
            "request": request,
            "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
            **detail_context,
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
    from app.services.crm.inbox.page_context import build_inbox_contact_detail_context

    detail_context = build_inbox_contact_detail_context(
        db,
        contact_id=contact_id,
        conversation_id=conversation_id,
    )
    if not detail_context:
        return HTMLResponse("<div class='p-8 text-center text-slate-500'>Contact not found</div>")
    from app.logic import private_note_logic

    return templates.TemplateResponse(
        "admin/crm/_contact_details.html",
        {
            "request": request,
            "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
            **detail_context,
        },
    )
