"""Admin projects web routes."""

import logging
from datetime import datetime
from html import escape as html_escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.projects import (
    Project,
    ProjectPriority,
    ProjectStatus,
    ProjectTask,
    ProjectTaskAssignee,
    ProjectTemplateTask,
    ProjectTemplateTaskDependency,
    ProjectType,
    TaskDependencyType,
    TaskPriority,
    TaskStatus,
)
from app.models.subscriber import Subscriber
from app.models.workflow import SlaClock, SlaClockStatus, WorkflowEntityType
from app.schemas.projects import (
    ProjectCommentCreate,
    ProjectCreate,
    ProjectTaskCommentCreate,
    ProjectTaskCreate,
    ProjectTaskUpdate,
    ProjectTemplateCreate,
    ProjectTemplateTaskCreate,
    ProjectTemplateTaskUpdate,
    ProjectTemplateUpdate,
    ProjectUpdate,
)
from app.schemas.vendor import InstallationProjectCreate, InstallationProjectUpdate
from app.services import audit as audit_service
from app.services import filter_preferences as filter_preferences_service
from app.services import person as person_service
from app.services import projects as projects_service
from app.services import settings_spec
from app.services import vendor as vendor_service
from app.services.audit_helpers import (
    build_changes_metadata,
    extract_changes,
    format_changes,
    log_audit_event,
)
from app.services.auth_dependencies import require_permission
from app.services.common import coerce_uuid
from app.services.filter_engine import parse_filter_payload_json

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/projects", tags=["web-admin-projects"])
REGION_OPTIONS = ["Gudu", "Garki", "Gwarimpa", "Jabi", "Lagos"]


class _TemplateTaskJSONItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_id: str
    title: str
    description: str = ""
    effort_hours: int | str | None = None
    dependencies: list[str] = Field(default_factory=list)


class _MentionJSONItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    agent_id: str | None = None
    person_id: str | None = None

    @model_validator(mode="after")
    def _validate_identifier(self):
        if self.id or self.agent_id or self.person_id:
            return self
        raise ValueError("Each mention object must include id, agent_id, or person_id.")


_TEMPLATE_TASKS_JSON_ADAPTER = TypeAdapter(list[_TemplateTaskJSONItem])
_MENTIONS_JSON_ADAPTER = TypeAdapter(list[str | _MentionJSONItem])


def _form_str(value: object | None) -> str:
    return value if isinstance(value, str) else ""


def _form_str_opt(value: object | None) -> str | None:
    value_str = _form_str(value).strip()
    return value_str or None


def _parse_datetime_opt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _format_validation_error(exc: ValidationError) -> str:
    first_error = exc.errors()[0] if exc.errors() else {"msg": "Invalid payload.", "loc": [], "type": "value_error", "input": None}
    loc_path = ".".join(str(part) for part in first_error.get("loc", []))
    message = str(first_error.get("msg", "Invalid payload."))
    return f"{loc_path}: {message}" if loc_path else message


def _parse_mentions_json(raw_mentions: str) -> list[str]:
    if not raw_mentions:
        return []
    parsed_mentions = _MENTIONS_JSON_ADAPTER.validate_json(raw_mentions)
    mention_ids: list[str] = []
    seen: set[str] = set()
    for mention in parsed_mentions:
        raw_mention_id = (
            mention if isinstance(mention, str) else (mention.id or mention.agent_id or mention.person_id or "")
        )
        mention_id = raw_mention_id.strip()
        if not mention_id or mention_id in seen:
            continue
        seen.add(mention_id)
        mention_ids.append(mention_id)
    return mention_ids


def _resolve_current_person_id(request: Request, current_user: dict | None) -> str | None:
    """Resolve current person ID for 'me' filters from auth/session context."""
    auth = getattr(request.state, "auth", None)
    candidate_ids: list[object] = []
    if isinstance(auth, dict):
        candidate_ids.append(auth.get("person_id"))
    if isinstance(current_user, dict):
        candidate_ids.append(current_user.get("person_id"))
        candidate_ids.append(current_user.get("id"))

    for raw_id in candidate_ids:
        if not raw_id:
            continue
        try:
            return str(coerce_uuid(str(raw_id)))
        except Exception:
            continue
    return None


def _log_activity(
    db: Session,
    request: Request,
    action: str,
    entity_type: str,
    entity_id: str,
    actor_id: str | None,
    metadata: dict | None = None,
) -> None:
    log_audit_event(
        db=db,
        request=request,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        metadata=metadata,
    )


logger = logging.getLogger(__name__)


def _resolve_project_reference(db: Session, project_ref: str):
    if not project_ref:
        raise ValueError("Project not found")
    project = db.query(Project).filter(Project.number == project_ref).first()
    if project:
        return project, False
    project_uuid = coerce_uuid(project_ref)
    project = projects_service.projects.get(db=db, project_id=str(project_uuid))
    should_redirect = bool(project.number)
    return project, should_redirect


def _resolve_project_task_reference(db: Session, task_ref: str):
    if not task_ref:
        raise ValueError("Project task not found")
    task = (
        db.query(ProjectTask)
        .options(selectinload(ProjectTask.assignees).selectinload(ProjectTaskAssignee.person))
        .filter(ProjectTask.number == task_ref)
        .first()
    )
    if task:
        return task, False
    task_uuid = coerce_uuid(task_ref)
    task = (
        db.query(ProjectTask)
        .options(selectinload(ProjectTask.assignees).selectinload(ProjectTaskAssignee.person))
        .filter(ProjectTask.id == task_uuid)
        .first()
    )
    if not task:
        task = projects_service.project_tasks.get(db=db, task_id=str(task_uuid))
    should_redirect = bool(task.number)
    return task, should_redirect


def _format_activity(event, label: str) -> str:
    action = (getattr(event, "action", "") or "").lower()
    metadata = getattr(event, "metadata_", None) or {}
    if action == "create":
        return f"Created {label}"
    if action == "update":
        return f"Updated {label}"
    if action == "comment":
        return "Added a comment"
    if action == "comment_edit":
        return "Edited a comment"
    if action == "status_change":
        from_status = metadata.get("from")
        to_status = metadata.get("to")
        if from_status and to_status:
            return f"Changed status from {from_status} to {to_status}"
        return "Changed status"
    if action == "priority_change":
        from_priority = metadata.get("from")
        to_priority = metadata.get("to")
        if from_priority and to_priority:
            return f"Changed priority from {from_priority} to {to_priority}"
        return "Changed priority"
    return action.replace("_", " ").title() or "Activity"


def _build_activity_feed(db: Session, events: list, label: str) -> list[dict]:
    actor_ids = {str(event.actor_id) for event in events if getattr(event, "actor_id", None)}
    people = {}
    if actor_ids:
        people = {str(person.id): person for person in db.query(Person).filter(Person.id.in_(actor_ids)).all()}
    activities = []
    for event in events:
        actor = people.get(str(event.actor_id)) if getattr(event, "actor_id", None) else None
        if actor:
            actor_name = f"{actor.first_name} {actor.last_name}"
            actor_url = f"/admin/crm/contacts/{actor.id}"
        else:
            actor_name = "System"
            actor_url = None
        metadata = getattr(event, "metadata_", None) or {}
        changes = extract_changes(metadata, getattr(event, "action", None))
        change_summary = format_changes(changes, max_items=2)
        activities.append(
            {
                "message": _format_activity(event, label),
                "change_summary": change_summary,
                "occurred_at": getattr(event, "occurred_at", None),
                "actor_name": actor_name,
                "actor_url": actor_url,
            }
        )
    return activities


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _project_form_context(
    request: Request,
    db: Session,
    project: dict,
    action_url: str,
    error: str | None = None,
    labels: dict | None = None,
):
    from app.web.admin import get_current_user, get_sidebar_stats

    template_items = projects_service.project_templates.list(
        db=db,
        project_type=None,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    template_map = {}
    for template in template_items:
        if template.project_type:
            template_map[template.project_type.value] = str(template.id)
    region_assignment_map = _load_region_assignment_map(db)
    context = {
        "request": request,
        "project": project,
        "project_templates": template_items,
        "project_template_map": template_map,
        "project_types": [item.value for item in ProjectType],
        "project_statuses": [item.value for item in ProjectStatus],
        "project_priorities": [item.value for item in ProjectPriority],
        "region_options": REGION_OPTIONS,
        "region_assignment_map": region_assignment_map,
        "action_url": action_url,
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        # Typeahead labels
        "subscriber_label": (labels or {}).get("subscriber_label"),
        "assigned_vendor_label": (labels or {}).get("assigned_vendor_label"),
        "owner_label": (labels or {}).get("owner_label"),
        "manager_label": (labels or {}).get("manager_label"),
        "project_manager_label": (labels or {}).get("project_manager_label"),
        "assistant_manager_label": (labels or {}).get("assistant_manager_label"),
    }
    if error:
        context["error"] = error
    return context


def _load_region_assignment_map(db: Session) -> dict[str, dict[str, str | None]]:
    raw_map = settings_spec.resolve_value(db, SettingDomain.projects, "region_pm_assignments")
    if not isinstance(raw_map, dict):
        return {}

    normalized: dict[str, dict[str, str | None]] = {}
    person_ids: set[str] = set()

    for region, entry in raw_map.items():
        if not isinstance(region, str):
            continue
        manager_id: str | None = None
        spc_id: str | None = None
        if isinstance(entry, dict):
            manager_id = entry.get("manager_person_id") or entry.get("project_manager_person_id")
            spc_id = (
                entry.get("spc_person_id")
                or entry.get("assistant_person_id")
                or entry.get("assistant_manager_person_id")
            )
        elif isinstance(entry, str):
            manager_id = entry

        clean_manager_id: str | None = None
        clean_spc_id: str | None = None
        if manager_id:
            try:
                with_uuid = coerce_uuid(manager_id)
                clean_manager_id = str(with_uuid)
                person_ids.add(clean_manager_id)
            except Exception:
                clean_manager_id = None
        if spc_id:
            try:
                with_uuid = coerce_uuid(spc_id)
                clean_spc_id = str(with_uuid)
                person_ids.add(clean_spc_id)
            except Exception:
                clean_spc_id = None

        normalized[region] = {
            "manager_person_id": clean_manager_id,
            "project_manager_person_id": clean_manager_id,
            "assistant_manager_person_id": clean_spc_id,
        }

    if not person_ids:
        return normalized

    people = db.query(Person).filter(Person.id.in_([coerce_uuid(person_id) for person_id in person_ids])).all()
    labels = {str(person.id): _person_filter_label(person) for person in people}

    for _region, entry in normalized.items():
        manager_id = entry.get("manager_person_id")
        spc_id = entry.get("assistant_manager_person_id")
        entry["manager_label"] = labels.get(manager_id, "") if manager_id else ""
        entry["project_manager_label"] = labels.get(manager_id, "") if manager_id else ""
        entry["assistant_manager_label"] = labels.get(spc_id, "") if spc_id else ""

    return normalized


def _task_form_context(
    request: Request,
    db: Session,
    task: dict,
    action_url: str,
    error: str | None = None,
):
    from app.web.admin import get_current_user, get_sidebar_stats

    projects = projects_service.projects.list(
        db=db,
        subscriber_id=None,
        status=None,
        project_type=None,
        priority=None,
        owner_person_id=None,
        manager_person_id=None,
        project_manager_person_id=None,
        assistant_manager_person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    from app.services import dispatch as dispatch_service

    people = person_service.people.list(
        db=db,
        email=None,
        status=None,
        party_status=None,
        organization_id=None,
        is_active=True,
        order_by="last_name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    technicians = sorted(
        technicians,
        key=lambda tech: (
            (tech.person.last_name or "").lower() if tech.person else "",
            (tech.person.first_name or "").lower() if tech.person else "",
        ),
    )
    context = {
        "request": request,
        "task": task,
        "projects": projects,
        "people": people,
        "technicians": technicians,
        "task_statuses": [item.value for item in TaskStatus],
        "task_priorities": [item.value for item in TaskPriority],
        "action_url": action_url,
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
    }
    if error:
        context["error"] = error
    return context


def _person_filter_label(person: Person) -> str:
    if person.display_name:
        return person.display_name
    full_name = f"{person.first_name or ''} {person.last_name or ''}".strip()
    if full_name:
        return full_name
    return person.email or str(person.id)


def _load_project_pm_spc_options(db: Session) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rows = (
        db.query(Project.project_manager_person_id, Project.assistant_manager_person_id)
        .filter(Project.is_active.is_(True))
        .all()
    )
    pm_ids = {str(manager_id) for manager_id, _ in rows if manager_id}
    spc_ids = {str(spc_id) for _, spc_id in rows if spc_id}
    all_ids = pm_ids | spc_ids
    if not all_ids:
        return [], []

    people = db.query(Person).filter(Person.id.in_([coerce_uuid(person_id) for person_id in all_ids])).all()
    labels = {str(person.id): _person_filter_label(person) for person in people}
    pm_options = [
        {"value": person_id, "label": labels[person_id]}
        for person_id in sorted(pm_ids, key=lambda pid: labels.get(pid, ""))
    ]
    spc_options = [
        {"value": person_id, "label": labels[person_id]}
        for person_id in sorted(spc_ids, key=lambda pid: labels.get(pid, ""))
    ]
    return pm_options, spc_options


@router.get(
    "",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:read"))],
)
def projects_list(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    project_type: str | None = None,
    pm: str | None = None,
    spc: str | None = None,
    notice: str | None = None,
    filters: str | None = None,
    order_by: str = Query("created_at"),
    order_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    """List all projects."""
    if order_by not in {"created_at", "name", "priority"}:
        order_by = "created_at"
    if order_dir not in {"asc", "desc"}:
        order_dir = "desc"
    offset = (page - 1) * per_page
    from app.csrf import get_csrf_token
    from app.web.admin import get_current_user, get_sidebar_stats

    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)
    current_person_id = _resolve_current_person_id(request, current_user)
    try:
        filters_payload = parse_filter_payload_json(filters)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    current_person_uuid = None
    if current_person_id:
        try:
            current_person_uuid = coerce_uuid(current_person_id)
        except Exception:
            current_person_uuid = None

    query_params_map = {key: value for key, value in request.query_params.items()}
    if current_person_uuid:
        if filter_preferences_service.has_managed_params(query_params_map, filter_preferences_service.PROJECTS_PAGE):
            state = filter_preferences_service.extract_managed_state(
                query_params_map,
                filter_preferences_service.PROJECTS_PAGE,
            )
            filter_preferences_service.save_preference(
                db,
                current_person_uuid,
                filter_preferences_service.PROJECTS_PAGE.key,
                state,
            )
        else:
            saved_state = filter_preferences_service.get_preference(
                db,
                current_person_uuid,
                filter_preferences_service.PROJECTS_PAGE.key,
            )
            if saved_state:
                merged = filter_preferences_service.merge_query_with_state(
                    query_params_map,
                    filter_preferences_service.PROJECTS_PAGE,
                    saved_state,
                )
                if merged != query_params_map:
                    target_url = request.url.path if not merged else f"{request.url.path}?{urlencode(merged)}"
                    return RedirectResponse(url=target_url, status_code=302)

    pm_person_id = None
    if pm == "me":
        if not current_person_id:
            raise HTTPException(status_code=400, detail="Unable to resolve current user for PM filter")
        pm_person_id = current_person_id
    elif pm:
        try:
            pm_person_id = str(coerce_uuid(pm))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid PM filter") from exc

    spc_person_id = None
    if spc == "me":
        if not current_person_id:
            raise HTTPException(status_code=400, detail="Unable to resolve current user for SPC filter")
        spc_person_id = current_person_id
    elif spc:
        try:
            spc_person_id = str(coerce_uuid(spc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid SPC filter") from exc

    projects = projects_service.projects.list(
        db=db,
        subscriber_id=None,
        status=status if status else None,
        project_type=project_type if project_type else None,
        priority=None,
        owner_person_id=None,
        manager_person_id=None,
        project_manager_person_id=pm_person_id,
        assistant_manager_person_id=spc_person_id,
        is_active=None,
        order_by=order_by,
        order_dir=order_dir,
        limit=per_page,
        offset=offset,
        search=search,
        filters_payload=filters_payload,
    )

    all_projects = projects_service.projects.list(
        db=db,
        subscriber_id=None,
        status=status if status else None,
        project_type=project_type if project_type else None,
        priority=None,
        owner_person_id=None,
        manager_person_id=None,
        project_manager_person_id=pm_person_id,
        assistant_manager_person_id=spc_person_id,
        is_active=None,
        order_by=order_by,
        order_dir=order_dir,
        limit=10000,
        offset=0,
        search=search,
        filters_payload=filters_payload,
    )
    total = len(all_projects)
    total_pages = (total + per_page - 1) // per_page if total else 1

    status_counts = {item.value: 0 for item in ProjectStatus}
    all_projects_unfiltered = projects_service.projects.list(
        db=db,
        subscriber_id=None,
        status=None,
        project_type=project_type if project_type else None,
        priority=None,
        owner_person_id=None,
        manager_person_id=None,
        project_manager_person_id=pm_person_id,
        assistant_manager_person_id=spc_person_id,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
        search=search,
        filters_payload=filters_payload,
    )
    for project in all_projects_unfiltered:
        status_value = project.status.value if project.status else ProjectStatus.open.value
        status_counts[status_value] = status_counts.get(status_value, 0) + 1
    total_count = len(all_projects_unfiltered)
    pm_options, spc_options = _load_project_pm_spc_options(db)
    csrf_token = get_csrf_token(request)

    return templates.TemplateResponse(
        "admin/projects/index.html",
        {
            "request": request,
            "projects": projects,
            "status": status,
            "search": search,
            "project_type": project_type,
            "project_types": [item.value for item in ProjectType],
            "pm": pm,
            "spc": spc,
            "filters": filters,
            "pm_options": pm_options,
            "spc_options": spc_options,
            "order_by": order_by,
            "order_dir": order_dir,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "status_counts": status_counts,
            "total_count": total_count,
            "csrf_token": csrf_token,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "notice": notice,
        },
    )


@router.get(
    "/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:create"))],
)
def project_new(request: Request, db: Session = Depends(get_db)):
    project = {
        "name": "",
        "code": "",
        "description": "",
        "customer_address": "",
        "project_type": "",
        "status": ProjectStatus.open.value,
        "priority": ProjectPriority.normal.value,
        "project_template_id": "",
        "subscriber_id": "",
        "owner_person_id": "",
        "manager_person_id": "",
        "project_manager_person_id": "",
        "assistant_manager_person_id": "",
        "start_at": "",
        "due_at": "",
        "completed_at": "",
        "region": "",
        "is_active": True,
    }
    context = _project_form_context(request, db, project, "/admin/projects")
    return templates.TemplateResponse("admin/projects/project_form.html", context)


@router.post(
    "",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:create"))],
)
async def project_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    attachments = form.getlist("attachments")
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    project = {
        "name": _form_str(form.get("name")).strip(),
        "code": _form_str(form.get("code")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "customer_address": _form_str(form.get("customer_address")).strip(),
        "project_type": _form_str(form.get("project_type")).strip(),
        "project_template_id": _form_str(form.get("project_template_id")).strip(),
        "assigned_vendor_id": _form_str(form.get("assigned_vendor_id")).strip(),
        "status": _form_str(form.get("status")).strip(),
        "priority": _form_str(form.get("priority")).strip(),
        "subscriber_id": _form_str(form.get("subscriber_id")).strip(),
        "owner_person_id": _form_str(form.get("owner_person_id")).strip(),
        "manager_person_id": _form_str(form.get("manager_person_id")).strip(),
        "project_manager_person_id": _form_str(form.get("project_manager_person_id")).strip(),
        "assistant_manager_person_id": _form_str(form.get("assistant_manager_person_id")).strip(),
        "start_at": _form_str(form.get("start_at")).strip(),
        "due_at": _form_str(form.get("due_at")).strip(),
        "region": _form_str(form.get("region")).strip(),
        "is_active": form.get("is_active") == "true",
    }
    if not project["name"]:
        context = _project_form_context(request, db, project, "/admin/projects", "Name is required.")
        return templates.TemplateResponse("admin/projects/project_form.html", context)

    payload_data = {
        "name": project["name"],
        "status": project["status"] or ProjectStatus.open.value,
        "priority": project["priority"] or ProjectPriority.normal.value,
        "is_active": project["is_active"],
    }
    if project["code"]:
        payload_data["code"] = project["code"]
    if project["description"]:
        payload_data["description"] = project["description"]
    if project["customer_address"]:
        payload_data["customer_address"] = project["customer_address"]
    if project["project_type"]:
        payload_data["project_type"] = project["project_type"]
    if project["project_template_id"]:
        payload_data["project_template_id"] = project["project_template_id"]
    if project["subscriber_id"]:
        payload_data["subscriber_id"] = project["subscriber_id"]
    if project["owner_person_id"]:
        payload_data["owner_person_id"] = project["owner_person_id"]
    if project["manager_person_id"]:
        payload_data["manager_person_id"] = project["manager_person_id"]
    if project["project_manager_person_id"]:
        payload_data["project_manager_person_id"] = project["project_manager_person_id"]
    if project["assistant_manager_person_id"]:
        payload_data["assistant_manager_person_id"] = project["assistant_manager_person_id"]
    if project["start_at"]:
        payload_data["start_at"] = project["start_at"]
    if project["due_at"]:
        payload_data["due_at"] = project["due_at"]
    if project["region"]:
        payload_data["region"] = project["region"]
    if current_user and current_user.get("person_id"):
        payload_data["created_by_person_id"] = current_user.get("person_id")

    prepared_attachments: list[dict] = []
    notice = None
    person = None
    if project["subscriber_id"]:
        subscriber = db.get(Subscriber, coerce_uuid(project["subscriber_id"]))
        person = subscriber.person if subscriber else None
    if not person and project["owner_person_id"]:
        person = db.get(Person, coerce_uuid(project["owner_person_id"]))
    if person and person.splynx_id:
        notice = "splynx_exists"

    try:
        from app.services import ticket_attachments as ticket_attachment_service

        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        if saved_attachments:
            payload_data["metadata_"] = {"attachments": saved_attachments}

        payload = ProjectCreate.model_validate(payload_data)
        created_project = projects_service.projects.create(db=db, payload=payload)
        _log_activity(
            db=db,
            request=request,
            action="create",
            entity_type="project",
            entity_id=str(created_project.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"name": created_project.name},
        )
        # Auto-create InstallationProject for installation types or if vendor assigned
        installation_types = {"fiber_optics_installation", "air_fiber_installation"}
        should_create_installation = project["assigned_vendor_id"] or project["project_type"] in installation_types
        if should_create_installation:
            install_payload = InstallationProjectCreate.model_validate(
                {
                    "project_id": created_project.id,
                    "assigned_vendor_id": project["assigned_vendor_id"] or None,
                    "subscriber_id": project["subscriber_id"] or None,
                }
            )
            vendor_service.installation_projects.create(db=db, payload=install_payload)
        redirect_url = "/admin/projects"
        if notice:
            redirect_url = f"{redirect_url}?notice={notice}"
        return RedirectResponse(redirect_url, status_code=303)
    except Exception as exc:
        from app.services import ticket_attachments as ticket_attachment_service

        db.rollback()
        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _project_form_context(
            request,
            db,
            project,
            "/admin/projects",
            error or "Please correct the highlighted fields.",
        )
        return templates.TemplateResponse("admin/projects/project_form.html", context)


@router.get(
    "/tasks",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:task:read"))],
)
def project_tasks_list(
    request: Request,
    project_id: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assigned: str | None = None,
    filters: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    """List project tasks across all projects."""
    offset = (page - 1) * per_page

    from app.csrf import get_csrf_token
    from app.web.admin import get_current_user, get_sidebar_stats

    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)
    current_person_id = _resolve_current_person_id(request, current_user)
    try:
        filters_payload = parse_filter_payload_json(filters)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    current_person_uuid = None
    if current_person_id:
        try:
            current_person_uuid = coerce_uuid(current_person_id)
        except Exception:
            current_person_uuid = None

    query_params_map = {key: value for key, value in request.query_params.items()}
    if current_person_uuid:
        if filter_preferences_service.has_managed_params(
            query_params_map,
            filter_preferences_service.PROJECT_TASKS_PAGE,
        ):
            state = filter_preferences_service.extract_managed_state(
                query_params_map,
                filter_preferences_service.PROJECT_TASKS_PAGE,
            )
            filter_preferences_service.save_preference(
                db,
                current_person_uuid,
                filter_preferences_service.PROJECT_TASKS_PAGE.key,
                state,
            )
        else:
            saved_state = filter_preferences_service.get_preference(
                db,
                current_person_uuid,
                filter_preferences_service.PROJECT_TASKS_PAGE.key,
            )
            if saved_state:
                merged = filter_preferences_service.merge_query_with_state(
                    query_params_map,
                    filter_preferences_service.PROJECT_TASKS_PAGE,
                    saved_state,
                )
                if merged != query_params_map:
                    target_url = request.url.path if not merged else f"{request.url.path}?{urlencode(merged)}"
                    return RedirectResponse(url=target_url, status_code=302)
    assigned_to_person_id = None
    if assigned == "me":
        if not current_person_id:
            raise HTTPException(status_code=400, detail="Unable to resolve current user for assignment filter")
        assigned_to_person_id = current_person_id

    tasks = projects_service.project_tasks.list(
        db=db,
        project_id=project_id if project_id else None,
        status=status if status else None,
        priority=priority if priority else None,
        assigned_to_person_id=assigned_to_person_id,
        parent_task_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
        include_assigned=True,
        filters_payload=filters_payload,
    )

    all_tasks = projects_service.project_tasks.list(
        db=db,
        project_id=project_id if project_id else None,
        status=status if status else None,
        priority=priority if priority else None,
        assigned_to_person_id=assigned_to_person_id,
        parent_task_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
        filters_payload=filters_payload,
    )
    total = len(all_tasks)
    total_pages = (total + per_page - 1) // per_page if total else 1

    projects = projects_service.projects.list(
        db=db,
        subscriber_id=None,
        status=None,
        project_type=None,
        priority=None,
        owner_person_id=None,
        manager_person_id=None,
        project_manager_person_id=None,
        assistant_manager_person_id=None,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=1000,
        offset=0,
    )
    project_ids = {task.project_id for task in tasks if task.project_id}
    if project_ids:
        project_rows = db.query(Project).filter(Project.id.in_(project_ids)).all()
    else:
        project_rows = []
    project_map = {str(project.id): project for project in project_rows}
    task_ids = [task.id for task in tasks]
    breached_ids: set[str] = set()
    if task_ids:
        breached_rows = (
            db.query(SlaClock.entity_id)
            .filter(SlaClock.entity_type == WorkflowEntityType.project_task)
            .filter(SlaClock.entity_id.in_(task_ids))
            .filter(SlaClock.status == SlaClockStatus.breached)
            .all()
        )
        breached_ids = {str(row[0]) for row in breached_rows if row and row[0]}

    return templates.TemplateResponse(
        "admin/projects/tasks.html",
        {
            "request": request,
            "tasks": tasks,
            "projects": projects,
            "project_map": project_map,
            "project_id": project_id,
            "status": status,
            "priority": priority,
            "breached_task_ids": breached_ids,
            "assigned": assigned,
            "filters": filters,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "csrf_token": get_csrf_token(request),
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
        },
    )


@router.get(
    "/tasks/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:task:write"))],
)
def project_task_new(request: Request, db: Session = Depends(get_db)):
    task = {
        "project_id": "",
        "title": "",
        "description": "",
        "status": TaskStatus.todo.value,
        "priority": TaskPriority.normal.value,
        "assigned_to_person_id": "",
        "assigned_to_person_ids": [],
        "created_by_person_id": "",
        "start_at": "",
        "due_at": "",
        "effort_hours": "",
    }
    context = _task_form_context(request, db, task, "/admin/projects/tasks")
    return templates.TemplateResponse("admin/projects/project_task_form.html", context)


@router.post(
    "/tasks",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:task:write"))],
)
async def project_task_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    attachments = form.getlist("attachments")
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    task = {
        "project_id": _form_str(form.get("project_id")).strip(),
        "title": _form_str(form.get("title")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "status": _form_str(form.get("status")).strip(),
        "priority": _form_str(form.get("priority")).strip(),
        "assigned_to_person_id": _form_str(form.get("assigned_to_person_id")).strip(),
        "assigned_to_person_ids": [],
        "created_by_person_id": _form_str(form.get("created_by_person_id")).strip(),
        "start_at": _form_str(form.get("start_at")).strip(),
        "due_at": _form_str(form.get("due_at")).strip(),
        "effort_hours": _form_str(form.get("effort_hours")).strip(),
    }
    form_assignee_ids: list[str] = [
        item
        for item in (form.getlist("assigned_to_person_ids[]") or form.getlist("assigned_to_person_ids"))
        if isinstance(item, str)
    ]
    if form_assignee_ids:
        task["assigned_to_person_ids"] = [item for item in form_assignee_ids if item]
    if not task["project_id"]:
        context = _task_form_context(request, db, task, "/admin/projects/tasks", "Project is required.")
        return templates.TemplateResponse("admin/projects/project_task_form.html", context)
    if not task["title"]:
        context = _task_form_context(request, db, task, "/admin/projects/tasks", "Title is required.")
        return templates.TemplateResponse("admin/projects/project_task_form.html", context)

    payload_data: dict[str, object] = {
        "project_id": task["project_id"],
        "title": task["title"],
        "status": task["status"] or TaskStatus.todo.value,
        "priority": task["priority"] or TaskPriority.normal.value,
    }
    if task["description"]:
        payload_data["description"] = task["description"]
    normalized_assignees = [item for item in (task.get("assigned_to_person_ids") or []) if item]
    primary_assignee = normalized_assignees[0] if normalized_assignees else task["assigned_to_person_id"]
    if primary_assignee:
        payload_data["assigned_to_person_id"] = primary_assignee
    if task.get("assigned_to_person_ids") is not None:
        payload_data["assigned_to_person_ids"] = normalized_assignees
    if task["created_by_person_id"]:
        payload_data["created_by_person_id"] = task["created_by_person_id"]
    elif current_user and current_user.get("person_id"):
        payload_data["created_by_person_id"] = current_user.get("person_id")
    if task["start_at"]:
        payload_data["start_at"] = task["start_at"]
    if task["due_at"]:
        payload_data["due_at"] = task["due_at"]
    if task["effort_hours"]:
        payload_data["effort_hours"] = task["effort_hours"]

    prepared_attachments: list[dict] = []
    try:
        from app.services import ticket_attachments as ticket_attachment_service

        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        if saved_attachments:
            payload_data["metadata_"] = {"attachments": saved_attachments}

        payload = ProjectTaskCreate.model_validate(payload_data)
        created_task = projects_service.project_tasks.create(db=db, payload=payload)
        _log_activity(
            db=db,
            request=request,
            action="create",
            entity_type="project_task",
            entity_id=str(created_task.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"title": created_task.title},
        )
        return RedirectResponse("/admin/projects/tasks", status_code=303)
    except Exception as exc:
        from app.services import ticket_attachments as ticket_attachment_service

        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _task_form_context(
            request,
            db,
            task,
            "/admin/projects/tasks",
            error or "Please correct the highlighted fields.",
        )
        return templates.TemplateResponse("admin/projects/project_task_form.html", context)


def _fmt_dt(value):
    if not value:
        return ""
    return value.strftime("%Y-%m-%dT%H:%M")


def _template_form_context(
    request: Request,
    db: Session,
    template: dict,
    action_url: str,
    error: str | None = None,
):
    from app.web.admin import get_current_user, get_sidebar_stats

    context = {
        "request": request,
        "template": template,
        "project_types": [item.value for item in ProjectType],
        "action_url": action_url,
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
    }
    if error:
        context["error"] = error
    return context


def _template_task_form_context(
    request: Request,
    db: Session,
    template: dict,
    task: dict,
    action_url: str,
    error: str | None = None,
):
    from app.web.admin import get_current_user, get_sidebar_stats

    context = {
        "request": request,
        "template": template,
        "task": task,
        "action_url": action_url,
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
    }
    if error:
        context["error"] = error
    return context


def _build_template_task_editor_payload(db: Session, template_id: str) -> tuple[list, list[dict]]:
    tasks = projects_service.project_template_tasks.list(
        db=db,
        template_id=template_id,
        is_active=True,
        order_by="sort_order",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    task_ids = [task.id for task in tasks]
    dependencies_map: dict[str, list[str]] = {}
    if task_ids:
        dependencies = (
            db.query(ProjectTemplateTaskDependency)
            .filter(ProjectTemplateTaskDependency.template_task_id.in_(task_ids))
            .all()
        )
        for dependency in dependencies:
            dependencies_map.setdefault(str(dependency.template_task_id), []).append(
                str(dependency.depends_on_template_task_id)
            )
    payload = []
    for task in tasks:
        payload.append(
            {
                "client_id": str(task.id),
                "id": str(task.id),
                "title": task.title,
                "description": task.description or "",
                "effort_hours": task.effort_hours if task.effort_hours is not None else "",
                "dependencies": dependencies_map.get(str(task.id), []),
            }
        )
    return tasks, payload


@router.get(
    "/templates",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:read"))],
)
def project_templates_list(request: Request, db: Session = Depends(get_db)):
    try:
        template_items = projects_service.project_templates.list(
            db=db,
            project_type=None,
            is_active=True,
            order_by="name",
            order_dir="asc",
            limit=1000,
            offset=0,
        )
    except Exception:
        # Fallback to raw query to avoid enum coercion failures
        rows = (
            db.execute(
                text(
                    """
                    select id, name, project_type, description, is_active
                    from project_templates
                    where is_active = true
                    order by name asc
                    """
                )
            )
            .mappings()
            .all()
        )
        template_items = [dict(row) for row in rows]
    from app.web.admin import get_current_user, get_sidebar_stats

    return templates.TemplateResponse(
        "admin/projects/project_templates.html",
        {
            "request": request,
            "templates": template_items,
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get(
    "/templates/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
def project_template_new(request: Request, db: Session = Depends(get_db)):
    template = {
        "name": "",
        "project_type": "",
        "description": "",
        "is_active": True,
    }
    context = _template_form_context(request, db, template, "/admin/projects/templates")
    return templates.TemplateResponse("admin/projects/project_template_form.html", context)


@router.post(
    "/templates",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
async def project_template_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    template = {
        "name": _form_str(form.get("name")).strip(),
        "project_type": _form_str(form.get("project_type")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "is_active": form.get("is_active") == "true",
    }
    if not template["name"]:
        context = _template_form_context(request, db, template, "/admin/projects/templates", "Name is required.")
        return templates.TemplateResponse("admin/projects/project_template_form.html", context)
    payload_data = {
        "name": template["name"],
        "is_active": template["is_active"],
    }
    if template["project_type"]:
        payload_data["project_type"] = template["project_type"]
    if template["description"]:
        payload_data["description"] = template["description"]
    try:
        payload = ProjectTemplateCreate.model_validate(payload_data)
        projects_service.project_templates.create(db=db, payload=payload)
        return RedirectResponse("/admin/projects/templates", status_code=303)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _template_form_context(
            request,
            db,
            template,
            "/admin/projects/templates",
            error or "Please correct the highlighted fields.",
        )
        return templates.TemplateResponse("admin/projects/project_template_form.html", context)


@router.get(
    "/templates/{template_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:read"))],
)
def project_template_detail(request: Request, template_id: str, db: Session = Depends(get_db)):
    from app.csrf import get_csrf_token

    try:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
    except Exception:
        from app.web.admin import get_current_user, get_sidebar_stats

        context = {
            "request": request,
            "message": "Project template not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    tasks = projects_service.project_template_tasks.list(
        db=db,
        template_id=template_id,
        is_active=True,
        order_by="sort_order",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    from app.web.admin import get_current_user, get_sidebar_stats

    return templates.TemplateResponse(
        "admin/projects/project_template_detail.html",
        {
            "request": request,
            "template": template,
            "tasks": tasks,
            "csrf_token": get_csrf_token(request),
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get(
    "/templates/{template_id}/tasks/editor",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
def project_template_tasks_editor(request: Request, template_id: str, db: Session = Depends(get_db)):
    try:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
    except Exception:
        from app.web.admin import get_current_user, get_sidebar_stats

        context = {
            "request": request,
            "message": "Project template not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    _, tasks_payload = _build_template_task_editor_payload(db, template_id)
    from app.web.admin import get_current_user, get_sidebar_stats

    return templates.TemplateResponse(
        "admin/projects/project_template_tasks_editor.html",
        {
            "request": request,
            "template": template,
            "tasks_payload": tasks_payload,
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post(
    "/templates/{template_id}/tasks/editor",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
async def project_template_tasks_editor_update(request: Request, template_id: str, db: Session = Depends(get_db)):
    form = await request.form()
    raw_tasks = _form_str(form.get("tasks_json")).strip()
    tasks_error = "Tasks data is invalid. Please refresh and try again."
    try:
        tasks_data = _TEMPLATE_TASKS_JSON_ADAPTER.validate_json(raw_tasks) if raw_tasks else []
    except ValidationError as exc:
        tasks_data = None
        tasks_error = f"Tasks data is invalid: {_format_validation_error(exc)}"

    if tasks_data is None:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
        from app.web.admin import get_current_user, get_sidebar_stats

        return templates.TemplateResponse(
            "admin/projects/project_template_tasks_editor.html",
            {
                "request": request,
                "template": template,
                "tasks_payload": [],
                "error": tasks_error,
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )

    tasks_payload: list[dict] = []
    errors: list[str] = []
    seen_client_ids: set[str] = set()
    for item in tasks_data:
        client_id = item.client_id.strip()
        title = item.title.strip()
        description = item.description.strip()
        effort_hours_raw = "" if item.effort_hours is None else str(item.effort_hours).strip()
        dependencies = item.dependencies or []
        if not client_id:
            errors.append("Each task must have a client_id.")
        if not title:
            errors.append("Each task must have a title.")
        if client_id in seen_client_ids:
            errors.append("Duplicate task client_id found.")
        seen_client_ids.add(client_id)

        effort_hours: int | None = None
        if effort_hours_raw:
            try:
                effort_hours = int(effort_hours_raw)
            except ValueError:
                errors.append(f"Invalid effort_hours for task '{title}'.")

        tasks_payload.append(
            {
                "client_id": client_id,
                "title": title,
                "description": description,
                "effort_hours": effort_hours,
                "dependencies": dependencies,
            }
        )

    if errors:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
        from app.web.admin import get_current_user, get_sidebar_stats

        return templates.TemplateResponse(
            "admin/projects/project_template_tasks_editor.html",
            {
                "request": request,
                "template": template,
                "tasks_payload": tasks_payload,
                "error": " ".join(errors),
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )

    template_uuid = coerce_uuid(template_id)
    existing_tasks = db.query(ProjectTemplateTask).filter(ProjectTemplateTask.template_id == template_uuid).all()
    existing_map = {str(task.id): task for task in existing_tasks}
    client_id_to_task_id: dict[str, str] = {}
    kept_task_ids: set[str] = set()

    for index, task_data in enumerate(tasks_payload):
        client_id = task_data["client_id"]
        title = task_data["title"]
        description = task_data["description"]
        effort_hours = task_data.get("effort_hours")
        if client_id in existing_map:
            task = existing_map[client_id]
            task.title = title
            task.description = description or None
            task.sort_order = index
            task.effort_hours = effort_hours
            task.is_active = True
            kept_task_ids.add(str(task.id))
            client_id_to_task_id[client_id] = str(task.id)
        else:
            new_task = ProjectTemplateTask(
                template_id=template_uuid,
                title=title,
                description=description or None,
                sort_order=index,
                effort_hours=effort_hours,
                is_active=True,
            )
            db.add(new_task)
            db.flush()
            kept_task_ids.add(str(new_task.id))
            client_id_to_task_id[client_id] = str(new_task.id)

    for task in existing_tasks:
        if str(task.id) not in kept_task_ids:
            task.is_active = False

    template_task_ids = [str(task.id) for task in existing_tasks] + [
        task_id for task_id in kept_task_ids if task_id not in existing_map
    ]
    if template_task_ids:
        db.query(ProjectTemplateTaskDependency).filter(
            ProjectTemplateTaskDependency.template_task_id.in_(template_task_ids)
        ).delete(synchronize_session=False)

    dependency_pairs: set[tuple[str, str]] = set()
    for task_payload in tasks_payload:
        task_id = client_id_to_task_id.get(task_payload["client_id"])
        if not task_id:
            continue
        dependencies = task_payload.get("dependencies") or []
        if not isinstance(dependencies, list):
            continue
        for depends_on_client_id in dependencies:
            depends_on_id = client_id_to_task_id.get(str(depends_on_client_id))
            if not depends_on_id or depends_on_id == task_id:
                continue
            key = (task_id, depends_on_id)
            if key in dependency_pairs:
                continue
            dependency_pairs.add(key)
            db.add(
                ProjectTemplateTaskDependency(
                    template_task_id=task_id,
                    depends_on_template_task_id=depends_on_id,
                    dependency_type=TaskDependencyType.finish_to_start,
                    lag_days=0,
                )
            )

    db.commit()
    return RedirectResponse(f"/admin/projects/templates/{template_id}", status_code=303)


@router.get(
    "/templates/{template_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
def project_template_edit(request: Request, template_id: str, db: Session = Depends(get_db)):
    try:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
    except Exception:
        from app.web.admin import get_current_user, get_sidebar_stats

        context = {
            "request": request,
            "message": "Project template not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    template_data = {
        "id": str(template.id),
        "name": template.name or "",
        "project_type": template.project_type.value if template.project_type else "",
        "description": template.description or "",
        "is_active": bool(template.is_active),
    }
    context = _template_form_context(request, db, template_data, f"/admin/projects/templates/{template_id}/edit")
    return templates.TemplateResponse("admin/projects/project_template_form.html", context)


@router.post(
    "/templates/{template_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
async def project_template_update(request: Request, template_id: str, db: Session = Depends(get_db)):
    form = await request.form()
    template = {
        "id": template_id,
        "name": _form_str(form.get("name")).strip(),
        "project_type": _form_str(form.get("project_type")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "is_active": form.get("is_active") == "true",
    }
    if not template["name"]:
        context = _template_form_context(
            request,
            db,
            template,
            f"/admin/projects/templates/{template_id}/edit",
            "Name is required.",
        )
        return templates.TemplateResponse("admin/projects/project_template_form.html", context)
    payload_data = {
        "name": template["name"],
        "project_type": template["project_type"] or None,
        "description": template["description"] or None,
        "is_active": template["is_active"],
    }
    try:
        payload = ProjectTemplateUpdate.model_validate(payload_data)
        projects_service.project_templates.update(db=db, template_id=template_id, payload=payload)
        return RedirectResponse(f"/admin/projects/templates/{template_id}", status_code=303)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _template_form_context(
            request,
            db,
            template,
            f"/admin/projects/templates/{template_id}/edit",
            error or "Please correct the highlighted fields.",
        )
        return templates.TemplateResponse("admin/projects/project_template_form.html", context)


@router.post(
    "/templates/{template_id}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
def project_template_delete(request: Request, template_id: str, db: Session = Depends(get_db)):
    projects_service.project_templates.delete(db=db, template_id=template_id)
    return RedirectResponse("/admin/projects/templates", status_code=303)


@router.get(
    "/templates/{template_id}/tasks/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
def project_template_task_new(request: Request, template_id: str, db: Session = Depends(get_db)):
    template = projects_service.project_templates.get(db=db, template_id=template_id)
    task = {
        "title": "",
        "description": "",
        "sort_order": "",
    }
    context = _template_task_form_context(
        request,
        db,
        {"id": str(template.id), "name": template.name},
        task,
        f"/admin/projects/templates/{template_id}/tasks",
    )
    return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)


@router.post(
    "/templates/{template_id}/tasks",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
async def project_template_task_create(request: Request, template_id: str, db: Session = Depends(get_db)):
    form = await request.form()
    task = {
        "title": _form_str(form.get("title")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "sort_order": _form_str(form.get("sort_order")).strip(),
        "effort_hours": _form_str(form.get("effort_hours")).strip(),
    }
    if not task["title"]:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
        context = _template_task_form_context(
            request,
            db,
            {"id": str(template.id), "name": template.name},
            task,
            f"/admin/projects/templates/{template_id}/tasks",
            "Title is required.",
        )
        return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)
    payload_data: dict[str, object] = {
        "template_id": template_id,
        "title": task["title"],
        "description": task["description"] or None,
    }
    if task["effort_hours"]:
        try:
            payload_data["effort_hours"] = int(task["effort_hours"])
        except ValueError:
            template = projects_service.project_templates.get(db=db, template_id=template_id)
            context = _template_task_form_context(
                request,
                db,
                {"id": str(template.id), "name": template.name},
                task,
                f"/admin/projects/templates/{template_id}/tasks",
                "Effort hours must be a number.",
            )
            return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)
    if task["sort_order"]:
        try:
            payload_data["sort_order"] = int(task["sort_order"])
        except ValueError:
            template = projects_service.project_templates.get(db=db, template_id=template_id)
            context = _template_task_form_context(
                request,
                db,
                {"id": str(template.id), "name": template.name},
                task,
                f"/admin/projects/templates/{template_id}/tasks",
                "Sort order must be a number.",
            )
            return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)
    try:
        payload = ProjectTemplateTaskCreate.model_validate(payload_data)
        projects_service.project_template_tasks.create(db=db, payload=payload)
        return RedirectResponse(f"/admin/projects/templates/{template_id}", status_code=303)
    except Exception as exc:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _template_task_form_context(
            request,
            db,
            {"id": str(template.id), "name": template.name},
            task,
            f"/admin/projects/templates/{template_id}/tasks",
            error or "Please correct the highlighted fields.",
        )
        return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)


@router.get(
    "/templates/{template_id}/tasks/{task_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
def project_template_task_edit(request: Request, template_id: str, task_id: str, db: Session = Depends(get_db)):
    try:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
        task = projects_service.project_template_tasks.get(db=db, task_id=task_id)
        if str(task.template_id) != template_id:
            raise ValueError("Template mismatch")
    except Exception:
        from app.web.admin import get_current_user, get_sidebar_stats

        context = {
            "request": request,
            "message": "Project template task not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    task_data = {
        "id": str(task.id),
        "title": task.title or "",
        "description": task.description or "",
        "sort_order": task.sort_order if task.sort_order is not None else "",
        "effort_hours": task.effort_hours if task.effort_hours is not None else "",
    }
    context = _template_task_form_context(
        request,
        db,
        {"id": str(template.id), "name": template.name},
        task_data,
        f"/admin/projects/templates/{template_id}/tasks/{task_id}/edit",
    )
    return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)


@router.post(
    "/templates/{template_id}/tasks/{task_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
async def project_template_task_update(request: Request, template_id: str, task_id: str, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        existing_task = projects_service.project_template_tasks.get(db=db, task_id=task_id)
        if str(existing_task.template_id) != template_id:
            raise ValueError("Template mismatch")
    except Exception:
        from app.web.admin import get_current_user, get_sidebar_stats

        context = {
            "request": request,
            "message": "Project template task not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)
    task = {
        "title": _form_str(form.get("title")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "sort_order": _form_str(form.get("sort_order")).strip(),
        "effort_hours": _form_str(form.get("effort_hours")).strip(),
    }
    if not task["title"]:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
        context = _template_task_form_context(
            request,
            db,
            {"id": str(template.id), "name": template.name},
            task,
            f"/admin/projects/templates/{template_id}/tasks/{task_id}/edit",
            "Title is required.",
        )
        return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)
    payload_data: dict[str, object] = {
        "title": task["title"],
        "description": task["description"] or None,
    }
    if task["effort_hours"]:
        try:
            payload_data["effort_hours"] = int(task["effort_hours"])
        except ValueError:
            template = projects_service.project_templates.get(db=db, template_id=template_id)
            context = _template_task_form_context(
                request,
                db,
                {"id": str(template.id), "name": template.name},
                task,
                f"/admin/projects/templates/{template_id}/tasks/{task_id}/edit",
                "Effort hours must be a number.",
            )
            return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)
    if task["sort_order"]:
        try:
            payload_data["sort_order"] = int(task["sort_order"])
        except ValueError:
            template = projects_service.project_templates.get(db=db, template_id=template_id)
            context = _template_task_form_context(
                request,
                db,
                {"id": str(template.id), "name": template.name},
                task,
                f"/admin/projects/templates/{template_id}/tasks/{task_id}/edit",
                "Sort order must be a number.",
            )
            return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)
    try:
        payload = ProjectTemplateTaskUpdate.model_validate(payload_data)
        projects_service.project_template_tasks.update(db=db, task_id=task_id, payload=payload)
        return RedirectResponse(f"/admin/projects/templates/{template_id}", status_code=303)
    except Exception as exc:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _template_task_form_context(
            request,
            db,
            {"id": str(template.id), "name": template.name},
            task,
            f"/admin/projects/templates/{template_id}/tasks/{task_id}/edit",
            error or "Please correct the highlighted fields.",
        )
        return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)


@router.post(
    "/templates/{template_id}/tasks/{task_id}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
def project_template_task_delete(request: Request, template_id: str, task_id: str, db: Session = Depends(get_db)):
    try:
        existing_task = projects_service.project_template_tasks.get(db=db, task_id=task_id)
        if str(existing_task.template_id) != template_id:
            raise ValueError("Template mismatch")
    except Exception:
        from app.web.admin import get_current_user, get_sidebar_stats

        context = {
            "request": request,
            "message": "Project template task not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)
    projects_service.project_template_tasks.delete(db=db, task_id=task_id)
    return RedirectResponse(f"/admin/projects/templates/{template_id}", status_code=303)


@router.get(
    "/{project_ref}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:read"))],
)
def project_detail(request: Request, project_ref: str, db: Session = Depends(get_db)):
    from app.csrf import get_csrf_token
    from app.services.agent_mentions import list_active_users_for_mentions
    from app.web.admin import get_current_user, get_sidebar_stats

    try:
        project, should_redirect = _resolve_project_reference(db, project_ref)
        if should_redirect:
            return RedirectResponse(url=f"/admin/projects/{project.number}", status_code=302)
    except Exception:
        context = {
            "request": request,
            "message": "Project not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    tasks = projects_service.project_tasks.list(
        db=db,
        project_id=str(project.id),
        status=None,
        priority=None,
        assigned_to_person_id=None,
        parent_task_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=20,
        offset=0,
    )
    audit_events = audit_service.audit_events.list(
        db=db,
        actor_id=None,
        actor_type=None,
        action=None,
        entity_type="project",
        entity_id=str(project.id),
        request_id=None,
        is_success=None,
        status_code=None,
        is_active=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    activities = _build_activity_feed(db, audit_events, "project")
    comments = projects_service.project_comments.list(
        db=db,
        project_id=str(project.id),
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    customer_name = None
    if project.lead and project.lead.person:
        person = project.lead.person
        customer_name = person.display_name or f"{person.first_name} {person.last_name}".strip() or person.email
    elif project.subscriber:
        customer_name = project.subscriber.display_name
    customer_address = project.customer_address or (project.lead.address if project.lead else None)
    installation_projects = vendor_service.installation_projects.list(
        db=db,
        status=None,
        vendor_id=None,
        subscriber_id=None,
        project_id=str(project.id),
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=1,
        offset=0,
    )
    assigned_vendor = None
    if installation_projects and installation_projects[0].assigned_vendor_id:
        try:
            assigned_vendor = vendor_service.vendors.get(
                db=db, vendor_id=str(installation_projects[0].assigned_vendor_id)
            )
        except Exception:
            assigned_vendor = None

    # Fetch expense totals from ERP (cached to avoid blocking)
    expense_totals = None
    try:
        from app.services.dotmac_erp.cache import get_cached_expense_totals

        expense_totals = get_cached_expense_totals(db, "project", str(project.id))
    except Exception:
        logger.debug("ERP expense totals unavailable for project.", exc_info=True)

    # Fetch material requests linked to this project
    project_material_requests = []
    try:
        from app.services.material_requests import material_requests as mr_service

        project_material_requests = mr_service.list(
            db, project_id=str(project.id), order_by="created_at", order_dir="desc", limit=20, offset=0
        )
    except Exception:
        logger.debug("ERP expense totals fetch failed for project.", exc_info=True)

    return templates.TemplateResponse(
        "admin/projects/project_detail.html",
        {
            "request": request,
            "project": project,
            "tasks": tasks,
            "comments": comments,
            "activities": activities,
            "assigned_vendor": assigned_vendor,
            "expense_totals": expense_totals,
            "material_requests": project_material_requests,
            "customer_name": customer_name,
            "customer_address": customer_address,
            "csrf_token": get_csrf_token(request),
            "mention_agents": list_active_users_for_mentions(db),
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post(
    "/{project_ref}/comments",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
async def project_comment_create(request: Request, project_ref: str, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats

    form = await request.form()
    body = _form_str(form.get("body")).strip()
    mentions_raw = _form_str(form.get("mentions")).strip()
    attachments = form.getlist("attachments")
    if not body:
        return RedirectResponse(f"/admin/projects/{project_ref}", status_code=303)
    try:
        mentioned_agent_ids = _parse_mentions_json(mentions_raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid mentions payload: {_format_validation_error(exc)}"
        ) from exc
    prepared_attachments: list[dict] = []
    try:
        from app.services import ticket_attachments as ticket_attachment_service

        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        current_user = get_current_user(request)
        project, _should_redirect = _resolve_project_reference(db, project_ref)
        payload = ProjectCommentCreate.model_validate(
            {
                "project_id": str(project.id),
                "author_person_id": current_user.get("person_id") or None,
                "body": body,
                "attachments": saved_attachments or None,
            }
        )
        projects_service.project_comments.create(db=db, payload=payload)
        if mentioned_agent_ids:
            from app.services.agent_mentions import notify_agent_mentions

            preview = body
            if len(preview) > 140:
                preview = preview[:137].rstrip() + "..."
            ref = project.number or str(project.id)
            subtitle = f"Project {ref}"
            if project.name:
                subtitle = f"{subtitle} · {project.name}"
            notify_agent_mentions(
                db,
                mentioned_agent_ids=list(mentioned_agent_ids),
                actor_person_id=str(current_user.get("person_id")) if current_user else None,
                payload={
                    "kind": "mention",
                    "title": "Mentioned in project",
                    "subtitle": subtitle,
                    "preview": preview or None,
                    "target_url": f"/admin/projects/{project.number or project.id}",
                    "project_id": str(project.id),
                    "project_number": project.number,
                },
            )
        _log_activity(
            db=db,
            request=request,
            action="comment",
            entity_type="project",
            entity_id=str(project.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
        )
        return RedirectResponse(f"/admin/projects/{project.number or project.id}", status_code=303)
    except HTTPException as exc:
        from app.services import ticket_attachments as ticket_attachment_service

        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        logger.warning(
            "project_comment_attachment_error project_ref=%s detail=%s",
            project_ref,
            getattr(exc, "detail", None),
        )
        raise
    except Exception:
        from app.services import ticket_attachments as ticket_attachment_service

        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        logger.exception("project_comment_create_failed project_ref=%s", project_ref)
        context = {
            "request": request,
            "message": "Unable to add comment",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/500.html", context, status_code=500)


@router.post(
    "/{project_ref}/comments/{comment_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
async def project_comment_edit(request: Request, project_ref: str, comment_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats

    context = {
        "request": request,
        "message": "Editing comments is disabled.",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
    }
    return templates.TemplateResponse("admin/errors/403.html", context, status_code=403)


def _get_project_labels(db: Session, project, assigned_vendor_id: str | None = None) -> dict:
    """Build labels for project typeahead pre-population."""
    labels: dict[str, str | None] = {
        "subscriber_label": None,
        "assigned_vendor_label": None,
        "owner_label": None,
        "manager_label": None,
        "project_manager_label": None,
        "assistant_manager_label": None,
    }
    if project:
        if project.subscriber:
            base = project.subscriber.display_name
            if project.subscriber.subscriber_number:
                labels["subscriber_label"] = f"{base} ({project.subscriber.subscriber_number})"
            else:
                labels["subscriber_label"] = base
        if project.owner:
            labels["owner_label"] = f"{project.owner.first_name} {project.owner.last_name}"
        if project.manager:
            labels["manager_label"] = f"{project.manager.first_name} {project.manager.last_name}"
        if project.project_manager:
            labels["project_manager_label"] = (
                f"{project.project_manager.first_name} {project.project_manager.last_name}"
            )
        if project.assistant_manager:
            labels["assistant_manager_label"] = (
                f"{project.assistant_manager.first_name} {project.assistant_manager.last_name}"
            )
    if assigned_vendor_id:
        try:
            vendor = vendor_service.vendors.get(db=db, vendor_id=assigned_vendor_id)
            labels["assigned_vendor_label"] = vendor.name
        except Exception:
            logger.debug("Failed to resolve assigned vendor label.", exc_info=True)
    return labels


@router.get(
    "/{project_ref}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
def project_edit(request: Request, project_ref: str, db: Session = Depends(get_db)):
    try:
        project, should_redirect = _resolve_project_reference(db, project_ref)
        if should_redirect:
            return RedirectResponse(url=f"/admin/projects/{project.number}/edit", status_code=302)
    except Exception:
        from app.web.admin import get_current_user, get_sidebar_stats

        context = {
            "request": request,
            "message": "Project not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    project_data = {
        "id": str(project.id),
        "name": project.name or "",
        "code": project.code or "",
        "description": project.description or "",
        "customer_address": project.customer_address or "",
        "project_type": project.project_type.value if project.project_type else "",
        "project_template_id": str(project.project_template_id) if project.project_template_id else "",
        "assigned_vendor_id": "",
        "status": project.status.value if project.status else ProjectStatus.open.value,
        "priority": project.priority.value if project.priority else ProjectPriority.normal.value,
        "subscriber_id": str(project.subscriber_id) if project.subscriber_id else "",
        "owner_person_id": str(project.owner_person_id) if project.owner_person_id else "",
        "manager_person_id": str(project.manager_person_id) if project.manager_person_id else "",
        "project_manager_person_id": str(project.project_manager_person_id)
        if project.project_manager_person_id
        else "",
        "assistant_manager_person_id": str(project.assistant_manager_person_id)
        if project.assistant_manager_person_id
        else "",
        "start_at": _fmt_dt(project.start_at),
        "due_at": _fmt_dt(project.due_at),
        "completed_at": _fmt_dt(project.completed_at),
        "region": project.region or "",
        "is_active": bool(project.is_active),
        "attachments": [],
    }
    if project.metadata_:
        if isinstance(project.metadata_, dict):
            attachments = project.metadata_.get("attachments") or []
            if isinstance(attachments, list):
                project_data["attachments"] = attachments
        elif isinstance(project.metadata_, list):
            project_data["attachments"] = project.metadata_
    install_projects = vendor_service.installation_projects.list(
        db=db,
        status=None,
        vendor_id=None,
        subscriber_id=None,
        project_id=str(project.id),
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=1,
        offset=0,
    )
    assigned_vendor_id = None
    if install_projects and install_projects[0].assigned_vendor_id:
        assigned_vendor_id = str(install_projects[0].assigned_vendor_id)
        project_data["assigned_vendor_id"] = assigned_vendor_id

    labels = _get_project_labels(db, project, assigned_vendor_id)
    context = _project_form_context(
        request,
        db,
        project_data,
        f"/admin/projects/{project.number or project.id}/edit",
        labels=labels,
    )
    return templates.TemplateResponse("admin/projects/project_form.html", context)


@router.post(
    "/{project_ref}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
async def project_update(request: Request, project_ref: str, db: Session = Depends(get_db)):
    form = await request.form()
    attachments = form.getlist("attachments")
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    project_record, _should_redirect = _resolve_project_reference(db, project_ref)
    project_id = str(project_record.id)
    project = {
        "id": project_id,
        "name": _form_str(form.get("name")).strip(),
        "code": _form_str(form.get("code")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "customer_address": _form_str(form.get("customer_address")).strip(),
        "project_type": _form_str(form.get("project_type")).strip(),
        "project_template_id": _form_str(form.get("project_template_id")).strip(),
        "assigned_vendor_id": _form_str(form.get("assigned_vendor_id")).strip(),
        "status": _form_str(form.get("status")).strip(),
        "priority": _form_str(form.get("priority")).strip(),
        "subscriber_id": _form_str(form.get("subscriber_id")).strip(),
        "owner_person_id": _form_str(form.get("owner_person_id")).strip(),
        "manager_person_id": _form_str(form.get("manager_person_id")).strip(),
        "project_manager_person_id": _form_str(form.get("project_manager_person_id")).strip(),
        "assistant_manager_person_id": _form_str(form.get("assistant_manager_person_id")).strip(),
        "start_at": _form_str(form.get("start_at")).strip(),
        "due_at": _form_str(form.get("due_at")).strip(),
        "region": _form_str(form.get("region")).strip(),
        "is_active": form.get("is_active") == "true",
    }
    if not project["name"]:
        context = _project_form_context(
            request,
            db,
            project,
            f"/admin/projects/{project_record.number or project_id}/edit",
            "Name is required.",
        )
        return templates.TemplateResponse("admin/projects/project_form.html", context)

    payload_data = {
        "name": project["name"],
        "status": project["status"] or ProjectStatus.open.value,
        "priority": project["priority"] or ProjectPriority.normal.value,
        "is_active": project["is_active"],
    }
    if project["code"]:
        payload_data["code"] = project["code"]
    if project["description"]:
        payload_data["description"] = project["description"]
    if project["customer_address"]:
        payload_data["customer_address"] = project["customer_address"]
    if project["project_type"]:
        payload_data["project_type"] = project["project_type"]
    payload_data["project_template_id"] = project["project_template_id"] or None
    if project["subscriber_id"]:
        payload_data["subscriber_id"] = project["subscriber_id"]
    if project["owner_person_id"]:
        payload_data["owner_person_id"] = project["owner_person_id"]
    if project["manager_person_id"]:
        payload_data["manager_person_id"] = project["manager_person_id"]
    if project["project_manager_person_id"]:
        payload_data["project_manager_person_id"] = project["project_manager_person_id"]
    if project["assistant_manager_person_id"]:
        payload_data["assistant_manager_person_id"] = project["assistant_manager_person_id"]
    if project["start_at"]:
        payload_data["start_at"] = project["start_at"]
    if project["due_at"]:
        payload_data["due_at"] = project["due_at"]
    if project["region"]:
        payload_data["region"] = project["region"]

    prepared_attachments: list[dict] = []
    try:
        before = projects_service.projects.get(db=db, project_id=project_id)
        if attachments:
            from app.services import ticket_attachments as ticket_attachment_service

            prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
            saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
            if saved_attachments:
                existing_metadata = dict(before.metadata_) if isinstance(before.metadata_, dict) else {}
                existing_attachments = existing_metadata.get("attachments")
                attachment_list = list(existing_attachments) if isinstance(existing_attachments, list) else []
                attachment_list.extend(saved_attachments)
                existing_metadata["attachments"] = attachment_list
                payload_data["metadata_"] = existing_metadata

        payload = ProjectUpdate.model_validate(payload_data)
        after = projects_service.projects.update(db=db, project_id=project_id, payload=payload)
        metadata_payload = build_changes_metadata(before, after)
        _log_activity(
            db=db,
            request=request,
            action="update",
            entity_type="project",
            entity_id=str(project_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        # Auto-create/update InstallationProject for installation types or if vendor assigned
        installation_types = {"fiber_optics_installation", "air_fiber_installation"}
        should_have_installation = project["assigned_vendor_id"] or project["project_type"] in installation_types
        if should_have_installation:
            install_projects = vendor_service.installation_projects.list(
                db=db,
                status=None,
                vendor_id=None,
                subscriber_id=None,
                project_id=project_id,
                is_active=None,
                order_by="created_at",
                order_dir="desc",
                limit=1,
                offset=0,
            )
            if install_projects:
                if project["assigned_vendor_id"]:
                    update_payload = InstallationProjectUpdate.model_validate(
                        {"assigned_vendor_id": project["assigned_vendor_id"]}
                    )
                    vendor_service.installation_projects.update(
                        db=db, project_id=str(install_projects[0].id), payload=update_payload
                    )
            else:
                install_payload = InstallationProjectCreate.model_validate(
                    {
                        "project_id": project_id,
                        "assigned_vendor_id": project["assigned_vendor_id"] or None,
                        "subscriber_id": project["subscriber_id"] or None,
                    }
                )
                vendor_service.installation_projects.create(db=db, payload=install_payload)
        return RedirectResponse(f"/admin/projects/{project_record.number or project_id}", status_code=303)
    except Exception as exc:
        if prepared_attachments:
            from app.services import ticket_attachments as ticket_attachment_service

            ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _project_form_context(
            request,
            db,
            project,
            f"/admin/projects/{project_record.number or project_id}/edit",
            error or "Please correct the highlighted fields.",
        )
        return templates.TemplateResponse("admin/projects/project_form.html", context)


@router.post(
    "/{project_ref}/status",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
async def project_status_update(request: Request, project_ref: str, db: Session = Depends(get_db)):
    """Quick inline status update for a project."""
    from app.web.admin import get_current_user

    form = await request.form()
    status_raw = form.get("status")
    status_value = status_raw.strip() if isinstance(status_raw, str) else ""
    try:
        project, _should_redirect = _resolve_project_reference(db, project_ref)
        old_status = project.status.value if project.status else None
        payload = ProjectUpdate.model_validate({"status": status_value})
        projects_service.projects.update(db=db, project_id=str(project.id), payload=payload)
        current_user = get_current_user(request)
        _log_activity(
            db=db,
            request=request,
            action="status_change",
            entity_type="project",
            entity_id=str(project.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"from": old_status, "to": status_value},
        )
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                content="",
                headers={"HX-Redirect": f"/admin/projects/{project.number or project.id}"},
            )
        return RedirectResponse(f"/admin/projects/{project.number or project.id}", status_code=303)
    except Exception as exc:
        error = html_escape(exc.detail if hasattr(exc, "detail") else str(exc))
        if request.headers.get("HX-Request"):
            return HTMLResponse(content=f'<p class="text-red-600 text-sm">{error}</p>', status_code=422)
        return RedirectResponse(f"/admin/projects/{project_ref}", status_code=303)


@router.post(
    "/{project_ref}/priority",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:update"))],
)
async def project_priority_update(request: Request, project_ref: str, db: Session = Depends(get_db)):
    """Quick inline priority update for a project."""
    from app.web.admin import get_current_user

    form = await request.form()
    priority_raw = form.get("priority")
    priority_value = priority_raw.strip() if isinstance(priority_raw, str) else ""
    try:
        project, _should_redirect = _resolve_project_reference(db, project_ref)
        old_priority = project.priority.value if project.priority else None
        payload = ProjectUpdate.model_validate({"priority": priority_value})
        projects_service.projects.update(db=db, project_id=str(project.id), payload=payload)
        current_user = get_current_user(request)
        _log_activity(
            db=db,
            request=request,
            action="priority_change",
            entity_type="project",
            entity_id=str(project.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"from": old_priority, "to": priority_value},
        )
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                content="",
                headers={"HX-Redirect": f"/admin/projects/{project.number or project.id}"},
            )
        return RedirectResponse(f"/admin/projects/{project.number or project.id}", status_code=303)
    except Exception as exc:
        error = html_escape(exc.detail if hasattr(exc, "detail") else str(exc))
        if request.headers.get("HX-Request"):
            return HTMLResponse(content=f'<p class="text-red-600 text-sm">{error}</p>', status_code=422)
        return RedirectResponse(f"/admin/projects/{project_ref}", status_code=303)


@router.post(
    "/{project_ref}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:delete"))],
)
def project_delete(request: Request, project_ref: str, db: Session = Depends(get_db)):
    project, _should_redirect = _resolve_project_reference(db, project_ref)
    projects_service.projects.delete(db=db, project_id=str(project.id))
    return RedirectResponse("/admin/projects", status_code=303)


@router.get(
    "/tasks/{task_ref}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:task:read"))],
)
def project_task_detail(request: Request, task_ref: str, db: Session = Depends(get_db)):
    from app.csrf import get_csrf_token
    from app.services.agent_mentions import list_active_users_for_mentions
    from app.web.admin import get_current_user, get_sidebar_stats

    try:
        task, should_redirect = _resolve_project_task_reference(db, task_ref)
        if should_redirect:
            return RedirectResponse(url=f"/admin/projects/tasks/{task.number}", status_code=302)
        project = projects_service.projects.get(db=db, project_id=str(task.project_id))
        comments = projects_service.project_task_comments.list(
            db=db,
            task_id=str(task.id),
            order_by="created_at",
            order_dir="desc",
            limit=200,
            offset=0,
        )
        audit_events = audit_service.audit_events.list(
            db=db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type="project_task",
            entity_id=str(task.id),
            request_id=None,
            is_success=None,
            status_code=None,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )
        activities = _build_activity_feed(db, audit_events, "task")
        task_is_breached = (
            db.query(SlaClock)
            .filter(SlaClock.entity_type == WorkflowEntityType.project_task)
            .filter(SlaClock.entity_id == task.id)
            .filter(SlaClock.status == SlaClockStatus.breached)
            .first()
            is not None
        )
    except Exception:
        context = {
            "request": request,
            "message": "Project task not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    return templates.TemplateResponse(
        "admin/projects/project_task_detail.html",
        {
            "request": request,
            "task": task,
            "project": project,
            "comments": comments,
            "activities": activities,
            "task_is_breached": task_is_breached,
            "csrf_token": get_csrf_token(request),
            "mention_agents": list_active_users_for_mentions(db),
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post(
    "/tasks/{task_ref}/comments",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:task:write"))],
)
async def project_task_comment_create(request: Request, task_ref: str, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats

    form = await request.form()
    body = _form_str(form.get("body")).strip()
    mentions_raw = _form_str(form.get("mentions")).strip()
    attachments = form.getlist("attachments")
    if not body:
        return RedirectResponse(f"/admin/projects/tasks/{task_ref}", status_code=303)
    try:
        mentioned_agent_ids = _parse_mentions_json(mentions_raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid mentions payload: {_format_validation_error(exc)}"
        ) from exc
    prepared_attachments: list[dict] = []
    try:
        from app.services import ticket_attachments as ticket_attachment_service

        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        current_user = get_current_user(request)
        task, _should_redirect = _resolve_project_task_reference(db, task_ref)
        payload = ProjectTaskCommentCreate.model_validate(
            {
                "task_id": str(task.id),
                "author_person_id": current_user.get("person_id") or None,
                "body": body,
                "attachments": saved_attachments or None,
            }
        )
        projects_service.project_task_comments.create(db=db, payload=payload)
        if mentioned_agent_ids:
            from app.services.agent_mentions import notify_agent_mentions

            preview = body
            if len(preview) > 140:
                preview = preview[:137].rstrip() + "..."
            ref = task.number or str(task.id)
            subtitle = f"Task {ref}"
            if task.title:
                subtitle = f"{subtitle} · {task.title}"
            notify_agent_mentions(
                db,
                mentioned_agent_ids=list(mentioned_agent_ids),
                actor_person_id=str(current_user.get("person_id")) if current_user else None,
                payload={
                    "kind": "mention",
                    "title": "Mentioned in task",
                    "subtitle": subtitle,
                    "preview": preview or None,
                    "target_url": f"/admin/projects/tasks/{task.number or task.id}",
                    "task_id": str(task.id),
                    "task_number": task.number,
                    "project_id": str(task.project_id) if getattr(task, "project_id", None) else None,
                },
            )
        _log_activity(
            db=db,
            request=request,
            action="comment",
            entity_type="project_task",
            entity_id=str(task.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
        )
        return RedirectResponse(f"/admin/projects/tasks/{task.number or task.id}", status_code=303)
    except HTTPException as exc:
        from app.services import ticket_attachments as ticket_attachment_service

        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        logger.warning(
            "project_task_comment_http_error task_ref=%s detail=%s",
            task_ref,
            getattr(exc, "detail", None),
        )
        raise
    except Exception:
        from app.services import ticket_attachments as ticket_attachment_service

        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        context = {
            "request": request,
            "message": "Unable to add comment",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/500.html", context, status_code=500)


@router.get(
    "/tasks/{task_ref}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:task:write"))],
)
def project_task_edit(request: Request, task_ref: str, db: Session = Depends(get_db)):
    try:
        task, should_redirect = _resolve_project_task_reference(db, task_ref)
        if should_redirect:
            return RedirectResponse(url=f"/admin/projects/tasks/{task.number}/edit", status_code=302)
    except Exception:
        from app.web.admin import get_current_user, get_sidebar_stats

        context = {
            "request": request,
            "message": "Project task not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    task_data = {
        "id": str(task.id),
        "project_id": str(task.project_id),
        "title": task.title or "",
        "description": task.description or "",
        "status": task.status.value if task.status else TaskStatus.todo.value,
        "priority": task.priority.value if task.priority else TaskPriority.normal.value,
        "assigned_to_person_id": str(task.assigned_to_person_id) if task.assigned_to_person_id else "",
        "assigned_to_person_ids": (
            [str(assignee.person_id) for assignee in (task.assignees or [])]
            or ([str(task.assigned_to_person_id)] if task.assigned_to_person_id else [])
        ),
        "created_by_person_id": str(task.created_by_person_id) if task.created_by_person_id else "",
        "start_at": _fmt_dt(task.start_at),
        "due_at": _fmt_dt(task.due_at),
        "effort_hours": str(task.effort_hours) if task.effort_hours is not None else "",
    }
    context = _task_form_context(
        request,
        db,
        task_data,
        f"/admin/projects/tasks/{task.number or task.id}/edit",
    )
    return templates.TemplateResponse("admin/projects/project_task_form.html", context)


@router.post(
    "/tasks/{task_ref}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:task:write"))],
)
async def project_task_update(request: Request, task_ref: str, db: Session = Depends(get_db)):
    form = await request.form()
    attachments = form.getlist("attachments")
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    task_record, _should_redirect = _resolve_project_task_reference(db, task_ref)
    task_id = str(task_record.id)
    task = {
        "id": task_id,
        "project_id": _form_str(form.get("project_id")).strip(),
        "title": _form_str(form.get("title")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "status": _form_str(form.get("status")).strip(),
        "priority": _form_str(form.get("priority")).strip(),
        "assigned_to_person_id": _form_str(form.get("assigned_to_person_id")).strip(),
        "assigned_to_person_ids": [],
        "created_by_person_id": _form_str(form.get("created_by_person_id")).strip(),
        "start_at": _form_str(form.get("start_at")).strip(),
        "due_at": _form_str(form.get("due_at")).strip(),
        "effort_hours": _form_str(form.get("effort_hours")).strip(),
    }
    form_assignee_ids: list[str] = [
        item
        for item in (form.getlist("assigned_to_person_ids[]") or form.getlist("assigned_to_person_ids"))
        if isinstance(item, str)
    ]
    if form_assignee_ids:
        task["assigned_to_person_ids"] = [item for item in form_assignee_ids if item]
    if not task["project_id"]:
        context = _task_form_context(
            request,
            db,
            task,
            f"/admin/projects/tasks/{task_record.number or task_id}/edit",
            "Project is required.",
        )
        return templates.TemplateResponse("admin/projects/project_task_form.html", context)
    if not task["title"]:
        context = _task_form_context(
            request,
            db,
            task,
            f"/admin/projects/tasks/{task_record.number or task_id}/edit",
            "Title is required.",
        )
        return templates.TemplateResponse("admin/projects/project_task_form.html", context)

    payload_data: dict[str, object] = {
        "project_id": task["project_id"],
        "title": task["title"],
        "status": task["status"] or TaskStatus.todo.value,
        "priority": task["priority"] or TaskPriority.normal.value,
    }
    if task["description"]:
        payload_data["description"] = task["description"]
    normalized_assignees = [item for item in (task.get("assigned_to_person_ids") or []) if item]
    primary_assignee = normalized_assignees[0] if normalized_assignees else task["assigned_to_person_id"]
    if primary_assignee:
        payload_data["assigned_to_person_id"] = primary_assignee
    if task.get("assigned_to_person_ids") is not None:
        payload_data["assigned_to_person_ids"] = normalized_assignees
    if task["created_by_person_id"]:
        payload_data["created_by_person_id"] = task["created_by_person_id"]
    if task["start_at"]:
        payload_data["start_at"] = task["start_at"]
    if task["due_at"]:
        payload_data["due_at"] = task["due_at"]
    if task["effort_hours"]:
        payload_data["effort_hours"] = task["effort_hours"]

    prepared_attachments: list[dict] = []
    try:
        before = projects_service.project_tasks.get(db=db, task_id=task_id)
        if attachments:
            from app.services import ticket_attachments as ticket_attachment_service

            prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
            saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
            if saved_attachments:
                existing_metadata = dict(before.metadata_) if isinstance(before.metadata_, dict) else {}
                existing_attachments = existing_metadata.get("attachments")
                attachment_list = list(existing_attachments) if isinstance(existing_attachments, list) else []
                attachment_list.extend(saved_attachments)
                existing_metadata["attachments"] = attachment_list
                payload_data["metadata_"] = existing_metadata

        payload = ProjectTaskUpdate.model_validate(payload_data)
        after = projects_service.project_tasks.update(db=db, task_id=task_id, payload=payload)
        metadata_payload = build_changes_metadata(before, after)
        _log_activity(
            db=db,
            request=request,
            action="update",
            entity_type="project_task",
            entity_id=str(task_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        return RedirectResponse(f"/admin/projects/tasks/{task_record.number or task_id}", status_code=303)
    except Exception as exc:
        if prepared_attachments:
            from app.services import ticket_attachments as ticket_attachment_service

            ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _task_form_context(
            request,
            db,
            task,
            f"/admin/projects/tasks/{task_record.number or task_id}/edit",
            error or "Please correct the highlighted fields.",
        )
        return templates.TemplateResponse("admin/projects/project_task_form.html", context)


@router.post(
    "/tasks/{task_ref}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("project:task:write"))],
)
def project_task_delete(request: Request, task_ref: str, db: Session = Depends(get_db)):
    task, _should_redirect = _resolve_project_task_reference(db, task_ref)
    projects_service.project_tasks.delete(db=db, task_id=str(task.id))
    return RedirectResponse("/admin/projects/tasks", status_code=303)
