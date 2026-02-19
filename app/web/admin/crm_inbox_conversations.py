"""CRM inbox conversation/list/detail partial routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal

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
    from app.services.crm.inbox.page_context import build_inbox_conversation_detail_context
    from app.web.admin import get_current_user

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
