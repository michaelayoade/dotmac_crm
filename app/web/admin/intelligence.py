"""Admin Intelligence Engine routes (insights dashboard)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.ai.insights import ai_insights
from app.web.admin import get_current_user, get_sidebar_stats

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/intelligence", tags=["web-admin-intelligence"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _can_reports_ops(user: dict | None) -> bool:
    user = user or {}
    perms = user.get("permissions") or []
    roles = user.get("roles") or []
    roles_lower = {str(r).lower() for r in roles}
    is_admin = "admin" in roles_lower
    return bool(is_admin or "reports:operations" in perms or "reports" in perms)


@router.get("/insights", response_class=HTMLResponse)
def insights_index(
    request: Request,
    domain: str | None = None,
    persona_key: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not _can_reports_ops(user):
        return templates.TemplateResponse(
            "admin/errors/403.html",
            {
                "request": request,
                "current_user": user,
                "sidebar_stats": get_sidebar_stats(db),
                "active_page": "ai-insights",
            },
            status_code=403,
        )

    items = ai_insights.list(
        db,
        domain=domain,
        persona_key=persona_key,
        entity_type=entity_type,
        entity_id=entity_id,
        status=status,
        severity=severity,
        limit=min(max(int(limit), 1), 200),
        offset=max(int(offset), 0),
    )
    return templates.TemplateResponse(
        "admin/intelligence/index.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "ai-insights",
            "items": items,
            "filters": {
                "domain": domain or "",
                "persona_key": persona_key or "",
                "entity_type": entity_type or "",
                "entity_id": entity_id or "",
                "status": status or "",
                "severity": severity or "",
                "limit": min(max(int(limit), 1), 200),
                "offset": max(int(offset), 0),
            },
        },
    )


@router.get("/insights/{insight_id}", response_class=HTMLResponse)
def insight_detail(
    request: Request,
    insight_id: str,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not _can_reports_ops(user):
        return templates.TemplateResponse(
            "admin/errors/403.html",
            {
                "request": request,
                "current_user": user,
                "sidebar_stats": get_sidebar_stats(db),
                "active_page": "ai-insights",
            },
            status_code=403,
        )

    insight = ai_insights.get(db, insight_id)
    return templates.TemplateResponse(
        "admin/intelligence/detail.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "ai-insights",
            "insight": insight,
        },
    )


@router.post("/insights/{insight_id}/acknowledge")
def acknowledge_insight(
    request: Request,
    insight_id: str,
    next: str | None = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not _can_reports_ops(user):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    person_id = str(user.get("person_id") or "").strip() or None
    if person_id:
        ai_insights.acknowledge(db, insight_id, person_id)

    redirect_to = next or f"/admin/intelligence/insights/{insight_id}"
    return RedirectResponse(url=redirect_to, status_code=303)
