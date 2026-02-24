"""Admin automation rules web routes."""

import json
import logging

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.automation_rule import AutomationRuleStatus
from app.schemas.automation_rule import AutomationRuleCreate, AutomationRuleUpdate
from app.services.auth_dependencies import require_permission
from app.services.automation_rules import AutomationRulesManager, automation_rules_service
from app.services.events.types import EventType
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/system/automations", tags=["web-admin-automations"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _base_ctx(request: Request, db: Session, **kwargs) -> dict:
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    return {
        "request": request,
        "current_user": current_user,
        "sidebar_stats": sidebar_stats,
        "active_page": "automations",
        "active_menu": "system",
        **kwargs,
    }


def _event_type_choices() -> list[dict[str, str]]:
    """Return event types as label/value pairs for form dropdowns."""
    return [{"value": et.value, "label": et.value} for et in EventType]


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:automation:read"))])
def automation_list(
    request: Request,
    db: Session = Depends(_get_db),
    status: str | None = Query(None),
    search: str | None = Query(None),
):
    items = automation_rules_service.list(db, status=status, search=search)
    status_counts = AutomationRulesManager.count_by_status(db)
    ctx = _base_ctx(
        request,
        db,
        rules=items,
        status_counts=status_counts,
        filter_status=status or "",
        search=search or "",
    )
    return templates.TemplateResponse("admin/system/automations.html", ctx)


# ── Create ────────────────────────────────────────────────────────────────────


@router.get("/new", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:automation:write"))])
def automation_create_form(request: Request, db: Session = Depends(_get_db)):
    ctx = _base_ctx(
        request,
        db,
        rule=None,
        event_types=_event_type_choices(),
        errors=[],
    )
    return templates.TemplateResponse("admin/system/automation_form.html", ctx)


@router.post("", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:automation:write"))])
def automation_create(
    request: Request,
    db: Session = Depends(_get_db),
    name: str = Form(...),
    description: str = Form(""),
    event_type: str = Form(...),
    priority: int = Form(0),
    cooldown_seconds: int = Form(0),
    stop_after_match: str = Form(""),
    conditions_json: str = Form("[]"),
    actions_json: str = Form("[]"),
):
    current_user = get_current_user(request)
    created_by_id = current_user.get("person_id") if current_user else None
    errors: list[str] = []

    try:
        conditions = json.loads(conditions_json)
    except (json.JSONDecodeError, TypeError):
        conditions = []
        errors.append("Invalid conditions JSON.")

    try:
        actions = json.loads(actions_json)
    except (json.JSONDecodeError, TypeError):
        actions = []
        errors.append("Invalid actions JSON.")

    if not actions:
        errors.append("At least one action is required.")

    if errors:
        ctx = _base_ctx(
            request,
            db,
            rule=None,
            event_types=_event_type_choices(),
            errors=errors,
        )
        return templates.TemplateResponse("admin/system/automation_form.html", ctx, status_code=400)

    payload = AutomationRuleCreate(
        name=name,
        description=description.strip() or None,
        event_type=event_type,
        conditions=conditions,
        actions=actions,
        priority=priority,
        cooldown_seconds=cooldown_seconds,
        stop_after_match=bool(stop_after_match),
    )
    rule = automation_rules_service.create(db, payload, created_by_id=created_by_id)
    return RedirectResponse(url=f"/admin/system/automations/{rule.id}", status_code=303)


# ── Detail ────────────────────────────────────────────────────────────────────


@router.get(
    "/{rule_id}", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:automation:read"))]
)
def automation_detail(
    request: Request,
    rule_id: str,
    db: Session = Depends(_get_db),
):
    rule = automation_rules_service.get(db, rule_id)
    logs = automation_rules_service.recent_logs(db, rule_id, limit=20)
    ctx = _base_ctx(request, db, rule=rule, logs=logs)
    return templates.TemplateResponse("admin/system/automation_detail.html", ctx)


# ── Edit ──────────────────────────────────────────────────────────────────────


@router.get(
    "/{rule_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:automation:write"))],
)
def automation_edit_form(
    request: Request,
    rule_id: str,
    db: Session = Depends(_get_db),
):
    rule = automation_rules_service.get(db, rule_id)
    ctx = _base_ctx(
        request,
        db,
        rule=rule,
        event_types=_event_type_choices(),
        errors=[],
    )
    return templates.TemplateResponse("admin/system/automation_form.html", ctx)


@router.post(
    "/{rule_id}", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:automation:write"))]
)
def automation_update(
    request: Request,
    rule_id: str,
    db: Session = Depends(_get_db),
    name: str = Form(...),
    description: str = Form(""),
    event_type: str = Form(...),
    priority: int = Form(0),
    cooldown_seconds: int = Form(0),
    stop_after_match: str = Form(""),
    conditions_json: str = Form("[]"),
    actions_json: str = Form("[]"),
):
    errors: list[str] = []

    try:
        conditions = json.loads(conditions_json)
    except (json.JSONDecodeError, TypeError):
        conditions = []
        errors.append("Invalid conditions JSON.")

    try:
        actions = json.loads(actions_json)
    except (json.JSONDecodeError, TypeError):
        actions = []
        errors.append("Invalid actions JSON.")

    if not actions:
        errors.append("At least one action is required.")

    if errors:
        rule = automation_rules_service.get(db, rule_id)
        ctx = _base_ctx(
            request,
            db,
            rule=rule,
            event_types=_event_type_choices(),
            errors=errors,
        )
        return templates.TemplateResponse("admin/system/automation_form.html", ctx, status_code=400)

    payload = AutomationRuleUpdate(
        name=name,
        description=description.strip() or None,
        event_type=event_type,
        conditions=conditions,
        actions=actions,
        priority=priority,
        cooldown_seconds=cooldown_seconds,
        stop_after_match=bool(stop_after_match),
    )
    automation_rules_service.update(db, rule_id, payload)
    return RedirectResponse(url=f"/admin/system/automations/{rule_id}", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────


@router.post("/{rule_id}/delete", dependencies=[Depends(require_permission("system:automation:write"))])
def automation_delete(
    request: Request,
    rule_id: str,
    db: Session = Depends(_get_db),
):
    automation_rules_service.delete(db, rule_id)
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": "/admin/system/automations"})
    return RedirectResponse(url="/admin/system/automations", status_code=303)


# ── Toggle ────────────────────────────────────────────────────────────────────


@router.post("/{rule_id}/toggle", dependencies=[Depends(require_permission("system:automation:write"))])
def automation_toggle(
    rule_id: str,
    db: Session = Depends(_get_db),
):
    rule = automation_rules_service.get(db, rule_id)
    new_status = (
        AutomationRuleStatus.paused if rule.status == AutomationRuleStatus.active else AutomationRuleStatus.active
    )
    automation_rules_service.toggle_status(db, rule_id, new_status)
    return RedirectResponse(url=f"/admin/system/automations/{rule_id}", status_code=303)


# ── HTMX Partials ────────────────────────────────────────────────────────────


@router.get(
    "/{rule_id}/logs", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:automation:read"))]
)
def automation_logs_partial(
    request: Request,
    rule_id: str,
    db: Session = Depends(_get_db),
):
    logs = automation_rules_service.recent_logs(db, rule_id, limit=50)
    ctx = _base_ctx(request, db, logs=logs, rule_id=rule_id)
    return templates.TemplateResponse("admin/system/_automation_logs.html", ctx)
