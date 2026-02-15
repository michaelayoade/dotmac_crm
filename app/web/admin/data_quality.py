"""Admin Data Quality dashboard routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.data_quality.reports import (
    DOMAIN_REPORTERS,
    all_domains_health,
    domain_health_report,
)
from app.web.admin import get_current_user, get_sidebar_stats

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/data-quality", tags=["web-admin-data-quality"])


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


@router.get("", response_class=HTMLResponse)
def data_quality_overview(
    request: Request,
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
                "active_page": "data-quality",
            },
            status_code=403,
        )

    reports = all_domains_health(db, limit=200)
    overall_avg = (
        round(sum(r.avg_quality for r in reports) / len(reports), 3)
        if reports
        else 0.0
    )
    total_entities = sum(r.entity_count for r in reports)

    return templates.TemplateResponse(
        "admin/data_quality/index.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "data-quality",
            "reports": reports,
            "overall_avg": overall_avg,
            "overall_avg_pct": round(overall_avg * 100),
            "total_entities": total_entities,
        },
    )


@router.get("/{domain}", response_class=HTMLResponse)
def data_quality_domain(
    request: Request,
    domain: str,
    limit: int = 100,
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
                "active_page": "data-quality",
            },
            status_code=403,
        )

    if domain not in DOMAIN_REPORTERS:
        return templates.TemplateResponse(
            "admin/errors/403.html",
            {
                "request": request,
                "current_user": user,
                "sidebar_stats": get_sidebar_stats(db),
                "active_page": "data-quality",
            },
            status_code=404,
        )

    report = domain_health_report(db, domain, limit=max(limit + offset, 200))

    # Get scored entities for the drill-down table
    from app.services.data_quality.reports import _get_scored_entities

    entities = _get_scored_entities(db, domain, limit=500)
    entities.sort(key=lambda r: r.score)
    total = len(entities)
    page = entities[offset: offset + limit]

    return templates.TemplateResponse(
        "admin/data_quality/domain_detail.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "data-quality",
            "report": report,
            "domain": domain,
            "entities": page,
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    )


@router.get("/{domain}/_table", response_class=HTMLResponse)
def data_quality_domain_table(
    request: Request,
    domain: str,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """HTMX partial: entity quality table."""
    user = get_current_user(request)
    if not _can_reports_ops(user):
        return HTMLResponse("Forbidden", status_code=403)

    if domain not in DOMAIN_REPORTERS:
        return HTMLResponse("Unknown domain", status_code=404)

    from app.services.data_quality.reports import _get_scored_entities

    entities = _get_scored_entities(db, domain, limit=500)
    entities.sort(key=lambda r: r.score)
    total = len(entities)
    page = entities[offset: offset + limit]

    return templates.TemplateResponse(
        "admin/data_quality/_entity_table.html",
        {
            "request": request,
            "entities": page,
            "domain": domain,
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    )
