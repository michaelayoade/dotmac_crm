"""CRM inbox comment partial routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
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


@router.get("/inbox/comments/list", response_class=HTMLResponse)
async def inbox_comments_list(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    comment_id: str | None = None,
    target_id: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    page: int | None = None,
):
    from app.services.crm.inbox.comments_context import load_comments_context

    safe_limit = max(int(limit or 150), 1)
    safe_page = max(int(page or 1), 1)
    safe_offset = max(int(offset or ((safe_page - 1) * safe_limit)), 0)
    context = await load_comments_context(
        db,
        search=search,
        comment_id=comment_id,
        offset=safe_offset,
        limit=safe_limit,
        fetch=True,
        target_id=target_id,
        include_thread=False,
    )
    template_name = "admin/crm/_comment_list_page.html" if safe_offset > 0 else "admin/crm/_comment_list.html"
    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "comments": context.grouped_comments,
            "selected_comment": context.selected_comment,
            "selected_comment_id": (
                str(getattr(context.selected_comment, "id", None))
                if getattr(context.selected_comment, "id", None) is not None
                else None
            ),
            "search": search,
            "current_target_id": target_id,
            "comments_has_more": context.has_more,
            "comments_next_offset": context.next_offset,
            "comments_limit": context.limit,
            "comments_page": (safe_offset // safe_limit) + 1,
            "comments_prev_page": (safe_page - 1) if safe_page > 1 else None,
            "comments_next_page": (safe_page + 1) if context.has_more else None,
        },
    )


@router.get("/inbox/comments/thread", response_class=HTMLResponse)
async def inbox_comments_thread(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    comment_id: str | None = None,
    target_id: str | None = None,
):
    from app.services.crm.inbox.comments_context import load_comments_context

    context = await load_comments_context(
        db,
        search=search,
        comment_id=comment_id,
        fetch=False,
        target_id=target_id,
    )
    return templates.TemplateResponse(
        "admin/crm/_comment_thread.html",
        {
            "request": request,
            "selected_comment": context.selected_comment,
            "comment_replies": context.comment_replies,
        },
    )
