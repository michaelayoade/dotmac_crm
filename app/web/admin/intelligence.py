"""Admin Intelligence Engine routes (insights dashboard)."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.web.templates import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.ai.client import AIClientError
from app.services.ai.data_health import (
    ALERT_SNOOZE_HOURS_ALLOWED,
    acknowledge_risk_alerts,
    build_data_health_baseline_snapshot,
    compute_effective_risk_alerts,
    compute_risk_inventory_deltas,
    get_data_health_report,
    get_data_health_trend,
    get_latest_data_health_baseline_snapshot,
    get_previous_data_health_baseline_snapshot,
    persist_data_health_baseline_snapshot,
    snooze_risk_alerts,
)
from app.services.ai.engine import intelligence_engine
from app.services.ai.insights import ai_insights
from app.services.ai.personas import persona_registry
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


def _create_redirect_url(
    *,
    error: str,
    persona_key: str,
    entity_type: str,
    entity_id: str | None,
    params_json: str,
) -> str:
    query = urlencode(
        {
            "create_error": error,
            "create_persona_key": persona_key,
            "create_entity_type": entity_type,
            "create_entity_id": entity_id or "",
            "create_params_json": params_json,
        }
    )
    return f"/admin/intelligence/insights?{query}"


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
            "personas": sorted(persona_registry.list_all(), key=lambda p: p.name.lower()),
            "create_form": {
                "persona_key": request.query_params.get("create_persona_key", "").strip(),
                "entity_type": request.query_params.get("create_entity_type", "").strip(),
                "entity_id": request.query_params.get("create_entity_id", "").strip(),
                "params_json": request.query_params.get("create_params_json", "").strip(),
                "error": request.query_params.get("create_error", "").strip(),
            },
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


@router.post("/insights/create")
async def create_insight(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not _can_reports_ops(user):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    form = await request.form()
    persona_key = str(form.get("persona_key") or "").strip()
    entity_type = str(form.get("entity_type") or "").strip()
    entity_id = str(form.get("entity_id") or "").strip() or None
    params_json = str(form.get("params_json") or "").strip()

    if not persona_key or not entity_type:
        return RedirectResponse(
            url=_create_redirect_url(
                error="Persona and entity type are required",
                persona_key=persona_key,
                entity_type=entity_type,
                entity_id=entity_id,
                params_json=params_json,
            ),
            status_code=303,
        )

    try:
        params = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError:
        return RedirectResponse(
            url=_create_redirect_url(
                error="Params must be valid JSON object",
                persona_key=persona_key,
                entity_type=entity_type,
                entity_id=entity_id,
                params_json=params_json,
            ),
            status_code=303,
        )

    if not isinstance(params, dict):
        return RedirectResponse(
            url=_create_redirect_url(
                error="Params must be a JSON object",
                persona_key=persona_key,
                entity_type=entity_type,
                entity_id=entity_id,
                params_json=params_json,
            ),
            status_code=303,
        )

    try:
        insight = intelligence_engine.invoke(
            db,
            persona_key=persona_key,
            params=params,
            entity_type=entity_type,
            entity_id=entity_id,
            trigger="on_demand",
            triggered_by_person_id=str(user.get("person_id")) if user else None,
        )
    except (AIClientError, ValueError) as exc:
        return RedirectResponse(
            url=_create_redirect_url(
                error=str(exc),
                persona_key=persona_key,
                entity_type=entity_type,
                entity_id=entity_id,
                params_json=params_json,
            ),
            status_code=303,
        )

    return RedirectResponse(url=f"/admin/intelligence/insights/{insight.id}", status_code=303)


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


@router.post("/insights/{insight_id}/action")
def action_insight(
    request: Request,
    insight_id: str,
    next: str | None = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not _can_reports_ops(user):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    person_id = str(user.get("person_id") or "").strip() or None
    ai_insights.action(db, insight_id, person_id=person_id)
    redirect_to = next or f"/admin/intelligence/insights/{insight_id}"
    return RedirectResponse(url=redirect_to, status_code=303)


@router.post("/insights/{insight_id}/expire")
def expire_insight(
    request: Request,
    insight_id: str,
    next: str | None = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not _can_reports_ops(user):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    ai_insights.expire(db, insight_id)
    redirect_to = next or f"/admin/intelligence/insights/{insight_id}"
    return RedirectResponse(url=redirect_to, status_code=303)


@router.get("/readiness", response_class=HTMLResponse)
def readiness_dashboard(
    request: Request,
    sample_limit: int = 20,
    days: int = 14,
    persona_key: str | None = None,
    domain: str | None = None,
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
                "active_page": "ai-readiness",
            },
            status_code=403,
        )

    health = get_data_health_report(db, sample_limit=sample_limit)
    trend = get_data_health_trend(db, days=days, persona_key=persona_key, domain=domain)
    baseline_snapshot = get_latest_data_health_baseline_snapshot(db)
    previous_baseline_snapshot = get_previous_data_health_baseline_snapshot(db)
    risk_deltas = compute_risk_inventory_deltas(baseline_snapshot, previous_baseline_snapshot)
    risk_alerts = compute_effective_risk_alerts(
        db,
        latest_snapshot=baseline_snapshot,
        previous_snapshot=previous_baseline_snapshot,
    )

    return templates.TemplateResponse(
        "admin/intelligence/readiness.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "ai-readiness",
            "health": health,
            "trend": trend,
            "baseline_snapshot": baseline_snapshot,
            "previous_baseline_snapshot": previous_baseline_snapshot,
            "risk_deltas": risk_deltas,
            "risk_alerts": risk_alerts,
            "personas": sorted(persona_registry.list_all(), key=lambda p: p.name.lower()),
            "filters": {
                "sample_limit": max(1, min(int(sample_limit), 100)),
                "days": max(1, min(int(days), 90)),
                "persona_key": persona_key or "",
                "domain": domain or "",
            },
            "alert_status": request.query_params.get("alert_status", "").strip(),
        },
    )


@router.post("/readiness/capture-baseline")
def readiness_capture_baseline(
    request: Request,
    sample_limit: int = Form(20),
    trend_days: int = Form(14),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not _can_reports_ops(user):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    snapshot = build_data_health_baseline_snapshot(db, sample_limit=sample_limit, trend_days=trend_days)
    persist_data_health_baseline_snapshot(db, snapshot)
    return RedirectResponse(
        url="/admin/intelligence/readiness?baseline_status=captured",
        status_code=303,
    )


@router.post("/readiness/alerts/ack")
def readiness_ack_alerts(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not _can_reports_ops(user):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    actor_person_id = str(user.get("person_id") or "").strip() or None
    acknowledge_risk_alerts(db, actor_person_id=actor_person_id)
    return RedirectResponse(url="/admin/intelligence/readiness?alert_status=acknowledged", status_code=303)


@router.post("/readiness/alerts/snooze")
def readiness_snooze_alerts(
    request: Request,
    hours: int = Form(24),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if not _can_reports_ops(user):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    if int(hours) not in ALERT_SNOOZE_HOURS_ALLOWED:
        return RedirectResponse(url="/admin/intelligence/readiness?alert_status=invalid_snooze", status_code=303)
    actor_person_id = str(user.get("person_id") or "").strip() or None
    snooze_risk_alerts(db, hours=hours, actor_person_id=actor_person_id)
    return RedirectResponse(url="/admin/intelligence/readiness?alert_status=snoozed", status_code=303)
