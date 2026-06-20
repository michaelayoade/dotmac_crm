"""Admin automation rules web routes."""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.automation_rule import AutomationRuleStatus
from app.models.dispatch import TechnicianProfile
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.projects import ProjectType
from app.models.service_team import ServiceTeam
from app.models.workflow import TicketAssignmentStrategy
from app.schemas.automation_rule import AutomationRuleCreate, AutomationRuleUpdate
from app.schemas.workflow import TicketAssignmentRuleCreate, TicketAssignmentRuleUpdate
from app.services import settings_spec
from app.services import workflow as workflow_service
from app.services.auth_dependencies import require_permission
from app.services.automation_rules import AutomationRulesManager, automation_rules_service
from app.services.common import coerce_uuid
from app.services.events.types import EventType
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.templates import Jinja2Templates

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


def _form_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _assignment_rule_match_config_from_form(form) -> dict | None:
    match_config_raw = str(form.get("match_config") or "").strip()
    match_config: dict[str, Any] = {}
    if match_config_raw:
        parsed = json.loads(match_config_raw)
        if not isinstance(parsed, dict):
            raise ValueError("Advanced match config must be a JSON object.")
        match_config.update(parsed)

    def _values(name: str) -> list[str]:
        raw_values = form.getlist(name) if hasattr(form, "getlist") else []
        return [str(value).strip() for value in raw_values if str(value).strip()]

    entity_types = _values("entity_types")
    project_types = _values("project_types")
    ticket_types = _values("ticket_types")
    ticket_types_extra = str(form.get("ticket_types_extra") or "").strip()
    if ticket_types_extra:
        for item in ticket_types_extra.replace("\r", "\n").split("\n"):
            value = item.strip()
            if value:
                ticket_types.append(value)

    assignee_person_id = str(form.get("assignee_person_id") or "").strip()

    if entity_types:
        match_config["entity_types"] = entity_types
    if project_types:
        match_config["project_types"] = project_types
    if ticket_types:
        match_config["ticket_types"] = list(dict.fromkeys(ticket_types))
    match_config["assignment_target"] = "technician"
    if assignee_person_id:
        match_config["assignee_person_id"] = assignee_person_id

    return match_config or None


def _assignment_rule_context(db: Session, selected_person_ids: set[str] | None = None) -> dict[str, object]:
    assignment_teams = (
        db.query(ServiceTeam).filter(ServiceTeam.is_active.is_(True)).order_by(ServiceTeam.name.asc()).all()
    )
    technician_person_ids = (
        db.query(TechnicianProfile.person_id).filter(TechnicianProfile.is_active.is_(True)).distinct().subquery()
    )
    assignment_people = (
        db.query(Person)
        .join(technician_person_ids, technician_person_ids.c.person_id == Person.id)
        .filter(Person.is_active.is_(True))
        .order_by(Person.display_name.asc().nulls_last(), Person.first_name.asc(), Person.last_name.asc())
        .limit(500)
        .all()
    )
    selected_ids = selected_person_ids or set()
    known_ids = {str(person.id) for person in assignment_people}
    missing_selected_ids = [person_id for person_id in selected_ids if person_id and person_id not in known_ids]
    if missing_selected_ids:
        selected_people = (
            db.query(Person)
            .filter(Person.id.in_(missing_selected_ids), Person.is_active.is_(True))
            .order_by(Person.display_name.asc().nulls_last(), Person.first_name.asc(), Person.last_name.asc())
            .all()
        )
        assignment_people = [*selected_people, *assignment_people]
    raw_ticket_types = settings_spec.resolve_value(db, SettingDomain.comms, "ticket_types")
    assignment_ticket_types: list[str] = []
    if isinstance(raw_ticket_types, list):
        for item in raw_ticket_types:
            if isinstance(item, dict) and item.get("is_active", True) and item.get("name"):
                assignment_ticket_types.append(str(item["name"]))
            elif isinstance(item, str) and item.strip():
                assignment_ticket_types.append(item.strip())

    return {
        "assignment_teams": assignment_teams,
        "assignment_people": assignment_people,
        "assignment_strategies": [item.value for item in TicketAssignmentStrategy],
        "assignment_project_types": [item.value for item in ProjectType],
        "assignment_ticket_types": assignment_ticket_types,
    }


def _assignment_rule_rows(db: Session) -> list[dict[str, object]]:
    rules = workflow_service.ticket_assignment_rules.list(
        db=db,
        strategy=None,
        is_active=None,
        order_by="priority",
        order_dir="desc",
        limit=100,
        offset=0,
    )
    assignee_ids = {
        str(config.get("assignee_person_id"))
        for rule in rules
        if isinstance((config := rule.match_config), dict) and config.get("assignee_person_id")
    }
    assignees: dict[str, str] = {}
    if assignee_ids:
        people = db.query(Person).filter(Person.id.in_(assignee_ids)).all()
        assignees = {
            str(person.id): person.display_name
            or " ".join(part for part in [person.first_name, person.last_name] if part).strip()
            or person.email
            or str(person.id)
            for person in people
        }

    rows: list[dict[str, object]] = []
    for rule in rules:
        config = rule.match_config if isinstance(rule.match_config, dict) else {}
        assignee_id = str(config.get("assignee_person_id") or "")
        rows.append(
            {
                "id": str(rule.id),
                "name": rule.name,
                "priority": rule.priority,
                "is_active": rule.is_active,
                "strategy": rule.strategy.value if rule.strategy else "",
                "target": "Technician",
                "assignee": assignees.get(assignee_id, assignee_id or "Team strategy"),
                "entity_types": config.get("entity_types") or [],
                "project_types": config.get("project_types") or [],
                "ticket_types": config.get("ticket_types") or [],
                "url": f"/admin/system/automations/assignment-rules/{rule.id}",
                "edit_url": f"/admin/system/automations/assignment-rules/{rule.id}/edit",
            }
        )
    return rows


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
        assignment_rules=_assignment_rule_rows(db),
        status_counts=status_counts,
        filter_status=status or "",
        search=search or "",
    )
    return templates.TemplateResponse("admin/system/automations.html", ctx)


# ── Create ────────────────────────────────────────────────────────────────────


@router.get("/new", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:automation:write"))])
def rule_type_choice(request: Request, db: Session = Depends(_get_db)):
    ctx = _base_ctx(request, db)
    return templates.TemplateResponse("admin/system/automation_rule_choice.html", ctx)


@router.get(
    "/automation-rules/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:automation:write"))],
)
def automation_create_form(request: Request, db: Session = Depends(_get_db)):
    ctx = _base_ctx(
        request,
        db,
        rule=None,
        event_types=_event_type_choices(),
        errors=[],
    )
    return templates.TemplateResponse("admin/system/automation_form.html", ctx)


@router.get(
    "/assignment-rules/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:automation:write"))],
)
def assignment_rule_create_form(request: Request, db: Session = Depends(_get_db)):
    ctx = _base_ctx(
        request,
        db,
        rule=None,
        errors=[],
        **_assignment_rule_context(db),
    )
    return templates.TemplateResponse("admin/system/assignment_rule_form.html", ctx)


@router.post(
    "/assignment-rules",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:automation:write"))],
)
async def assignment_rule_create(request: Request, db: Session = Depends(_get_db)):
    form = await request.form()
    try:
        priority_raw = str(form.get("priority") or "").strip()
        team_id_raw = str(form.get("team_id") or "").strip()
        payload = TicketAssignmentRuleCreate(
            name=str(form.get("name") or "").strip(),
            priority=int(priority_raw) if priority_raw else 0,
            is_active=_form_bool(form.get("is_active")),
            match_config=_assignment_rule_match_config_from_form(form),
            strategy=TicketAssignmentStrategy(str(form.get("strategy") or "round_robin").strip() or "round_robin"),
            team_id=coerce_uuid(team_id_raw) if team_id_raw else None,
            assign_manager=False,
            assign_spc=False,
        )
        rule = workflow_service.ticket_assignment_rules.create(db=db, payload=payload)
        return RedirectResponse(url=f"/admin/system/automations/assignment-rules/{rule.id}", status_code=303)
    except Exception as exc:
        ctx = _base_ctx(
            request,
            db,
            rule=None,
            errors=[str(exc)],
            **_assignment_rule_context(db),
        )
        return templates.TemplateResponse("admin/system/assignment_rule_form.html", ctx, status_code=400)


@router.get(
    "/assignment-rules/{rule_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:automation:read"))],
)
def assignment_rule_detail(request: Request, rule_id: str, db: Session = Depends(_get_db)):
    rule = workflow_service.ticket_assignment_rules.get(db=db, rule_id=rule_id)
    ctx = _base_ctx(
        request,
        db,
        rule=rule,
        row=next((item for item in _assignment_rule_rows(db) if item["id"] == str(rule.id)), None),
    )
    return templates.TemplateResponse("admin/system/assignment_rule_detail.html", ctx)


@router.get(
    "/assignment-rules/{rule_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:automation:write"))],
)
def assignment_rule_edit_form(request: Request, rule_id: str, db: Session = Depends(_get_db)):
    rule = workflow_service.ticket_assignment_rules.get(db=db, rule_id=rule_id)
    config = rule.match_config if isinstance(rule.match_config, dict) else {}
    selected_person_id = str(config.get("assignee_person_id") or "")
    ctx = _base_ctx(
        request,
        db,
        rule=rule,
        errors=[],
        **_assignment_rule_context(db, {selected_person_id} if selected_person_id else None),
    )
    return templates.TemplateResponse("admin/system/assignment_rule_form.html", ctx)


@router.post(
    "/assignment-rules/{rule_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:automation:write"))],
)
async def assignment_rule_update(request: Request, rule_id: str, db: Session = Depends(_get_db)):
    form = await request.form()
    try:
        existing = workflow_service.ticket_assignment_rules.get(db=db, rule_id=rule_id)
        priority_raw = str(form.get("priority") or "").strip()
        team_id_raw = str(form.get("team_id") or "").strip()
        strategy_raw = str(form.get("strategy") or "").strip()
        payload = TicketAssignmentRuleUpdate(
            name=str(form.get("name") or "").strip() or existing.name,
            priority=int(priority_raw) if priority_raw else existing.priority,
            is_active=_form_bool(form.get("is_active")),
            match_config=_assignment_rule_match_config_from_form(form),
            strategy=TicketAssignmentStrategy(strategy_raw) if strategy_raw else existing.strategy,
            team_id=coerce_uuid(team_id_raw) if team_id_raw else None,
            assign_manager=False,
            assign_spc=False,
        )
        workflow_service.ticket_assignment_rules.update(db=db, rule_id=rule_id, payload=payload)
        return RedirectResponse(url=f"/admin/system/automations/assignment-rules/{rule_id}", status_code=303)
    except Exception as exc:
        rule = workflow_service.ticket_assignment_rules.get(db=db, rule_id=rule_id)
        ctx = _base_ctx(
            request,
            db,
            rule=rule,
            errors=[str(exc)],
            **_assignment_rule_context(db),
        )
        return templates.TemplateResponse("admin/system/assignment_rule_form.html", ctx, status_code=400)


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
