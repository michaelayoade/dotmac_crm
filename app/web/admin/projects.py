"""Admin projects web routes."""

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from app.db import SessionLocal
from app.services import projects as projects_service
from app.services import audit as audit_service
from app.services.audit_helpers import (
    build_changes_metadata,
    extract_changes,
    format_changes,
    log_audit_event,
)
from app.models.person import Person
from app.services import person as person_service
from app.models.projects import ProjectPriority, ProjectStatus, ProjectType, TaskPriority, TaskStatus
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
from app.services import vendor as vendor_service
from app.services.common import coerce_uuid

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/projects", tags=["web-admin-projects"])


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


def _format_activity(event, label: str) -> str:
    action = (getattr(event, "action", "") or "").lower()
    metadata = getattr(event, "metadata_", None) or {}
    if action == "create":
        return f"Created {label}"
    if action == "update":
        return f"Updated {label}"
    if action == "comment":
        return "Added a comment"
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
        people = {
            str(person.id): person
            for person in db.query(Person).filter(Person.id.in_(actor_ids)).all()
        }
    activities = []
    for event in events:
        actor = people.get(str(event.actor_id)) if getattr(event, "actor_id", None) else None
        if actor:
            actor_name = f"{actor.first_name} {actor.last_name}"
            actor_url = f"/admin/customers/person/{actor.id}"
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
    from app.web.admin import get_sidebar_stats, get_current_user
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
    context = {
        "request": request,
        "project": project,
        "project_templates": template_items,
        "project_template_map": template_map,
        "project_types": [item.value for item in ProjectType],
        "project_statuses": [item.value for item in ProjectStatus],
        "project_priorities": [item.value for item in ProjectPriority],
        "action_url": action_url,
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        # Typeahead labels
        "subscriber_label": (labels or {}).get("subscriber_label"),
        "assigned_vendor_label": (labels or {}).get("assigned_vendor_label"),
        "owner_label": (labels or {}).get("owner_label"),
        "manager_label": (labels or {}).get("manager_label"),
    }
    if error:
        context["error"] = error
    return context


def _task_form_context(
    request: Request,
    db: Session,
    task: dict,
    action_url: str,
    error: str | None = None,
):
    from app.web.admin import get_sidebar_stats, get_current_user
    projects = projects_service.projects.list(
        db=db,
        subscriber_id=None,
        status=None,
        priority=None,
        owner_person_id=None,
        manager_person_id=None,
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


@router.get("", response_class=HTMLResponse)
def projects_list(
    request: Request,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    """List all projects."""
    offset = (page - 1) * per_page

    projects = projects_service.projects.list(
        db=db,
        subscriber_id=None,
        status=status if status else None,
        priority=priority if priority else None,
        owner_person_id=None,
        manager_person_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )

    all_projects = projects_service.projects.list(
        db=db,
        subscriber_id=None,
        status=status if status else None,
        priority=priority if priority else None,
        owner_person_id=None,
        manager_person_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    total = len(all_projects)
    total_pages = (total + per_page - 1) // per_page if total else 1

    status_counts = {item.value: 0 for item in ProjectStatus}
    all_projects_unfiltered = projects_service.projects.list(
        db=db,
        subscriber_id=None,
        status=None,
        priority=priority if priority else None,
        owner_person_id=None,
        manager_person_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    for project in all_projects_unfiltered:
        status_value = project.status.value if project.status else ProjectStatus.planned.value
        status_counts[status_value] = status_counts.get(status_value, 0) + 1
    total_count = len(all_projects_unfiltered)

    from app.web.admin import get_sidebar_stats, get_current_user
    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    return templates.TemplateResponse(
        "admin/projects/index.html",
        {
            "request": request,
            "projects": projects,
            "status": status,
            "priority": priority,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "status_counts": status_counts,
            "total_count": total_count,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def project_new(request: Request, db: Session = Depends(get_db)):
    project = {
        "name": "",
        "code": "",
        "description": "",
        "project_type": "",
        "status": ProjectStatus.planned.value,
        "priority": ProjectPriority.normal.value,
        "project_template_id": "",
        "subscriber_id": "",
        "owner_person_id": "",
        "manager_person_id": "",
        "start_at": "",
        "due_at": "",
        "is_active": True,
    }
    context = _project_form_context(request, db, project, "/admin/projects")
    return templates.TemplateResponse("admin/projects/project_form.html", context)


@router.post("", response_class=HTMLResponse)
async def project_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    attachments = [item for item in form.getlist("attachments") if isinstance(item, UploadFile)]
    from app.web.admin import get_current_user
    current_user = get_current_user(request)
    project = {
        "name": _form_str(form.get("name")).strip(),
        "code": _form_str(form.get("code")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "project_type": _form_str(form.get("project_type")).strip(),
        "project_template_id": _form_str(form.get("project_template_id")).strip(),
        "assigned_vendor_id": _form_str(form.get("assigned_vendor_id")).strip(),
        "status": _form_str(form.get("status")).strip(),
        "priority": _form_str(form.get("priority")).strip(),
        "subscriber_id": _form_str(form.get("subscriber_id")).strip(),
        "owner_person_id": _form_str(form.get("owner_person_id")).strip(),
        "manager_person_id": _form_str(form.get("manager_person_id")).strip(),
        "start_at": _form_str(form.get("start_at")).strip(),
        "due_at": _form_str(form.get("due_at")).strip(),
        "is_active": form.get("is_active") == "true",
    }
    if not project["name"]:
        context = _project_form_context(
            request, db, project, "/admin/projects", "Name is required."
        )
        return templates.TemplateResponse("admin/projects/project_form.html", context)

    payload_data = {
        "name": project["name"],
        "status": project["status"] or ProjectStatus.planned.value,
        "priority": project["priority"] or ProjectPriority.normal.value,
        "is_active": project["is_active"],
    }
    if project["code"]:
        payload_data["code"] = project["code"]
    if project["description"]:
        payload_data["description"] = project["description"]
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
    if project["start_at"]:
        payload_data["start_at"] = project["start_at"]
    if project["due_at"]:
        payload_data["due_at"] = project["due_at"]
    if current_user and current_user.get("person_id"):
        payload_data["created_by_person_id"] = current_user.get("person_id")

    prepared_attachments: list[dict] = []
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
        if project["assigned_vendor_id"]:
            install_payload = InstallationProjectCreate.model_validate(
                {
                    "project_id": created_project.id,
                    "assigned_vendor_id": project["assigned_vendor_id"],
                    "subscriber_id": project["subscriber_id"] or None,
                }
            )
            vendor_service.installation_projects.create(db=db, payload=install_payload)
        return RedirectResponse("/admin/projects", status_code=303)
    except Exception as exc:
        from app.services import ticket_attachments as ticket_attachment_service

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


@router.get("/tasks", response_class=HTMLResponse)
def project_tasks_list(
    request: Request,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    """List project tasks across all projects."""
    offset = (page - 1) * per_page

    tasks = projects_service.project_tasks.list(
        db=db,
        project_id=None,
        status=status if status else None,
        priority=priority if priority else None,
        assigned_to_person_id=None,
        parent_task_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )

    all_tasks = projects_service.project_tasks.list(
        db=db,
        project_id=None,
        status=status if status else None,
        priority=priority if priority else None,
        assigned_to_person_id=None,
        parent_task_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    total = len(all_tasks)
    total_pages = (total + per_page - 1) // per_page if total else 1

    from app.web.admin import get_sidebar_stats, get_current_user
    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    return templates.TemplateResponse(
        "admin/projects/tasks.html",
        {
            "request": request,
            "tasks": tasks,
            "status": status,
            "priority": priority,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
        },
    )


@router.get("/tasks/new", response_class=HTMLResponse)
def project_task_new(request: Request, db: Session = Depends(get_db)):
    task = {
        "project_id": "",
        "title": "",
        "description": "",
        "status": TaskStatus.todo.value,
        "priority": TaskPriority.normal.value,
        "assigned_to_person_id": "",
        "created_by_person_id": "",
        "start_at": "",
        "due_at": "",
        "effort_hours": "",
    }
    context = _task_form_context(request, db, task, "/admin/projects/tasks")
    return templates.TemplateResponse("admin/projects/project_task_form.html", context)


@router.post("/tasks", response_class=HTMLResponse)
async def project_task_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    attachments = [item for item in form.getlist("attachments") if isinstance(item, UploadFile)]
    from app.web.admin import get_current_user
    current_user = get_current_user(request)
    task = {
        "project_id": _form_str(form.get("project_id")).strip(),
        "title": _form_str(form.get("title")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "status": _form_str(form.get("status")).strip(),
        "priority": _form_str(form.get("priority")).strip(),
        "assigned_to_person_id": _form_str(form.get("assigned_to_person_id")).strip(),
        "created_by_person_id": _form_str(form.get("created_by_person_id")).strip(),
        "start_at": _form_str(form.get("start_at")).strip(),
        "due_at": _form_str(form.get("due_at")).strip(),
        "effort_hours": _form_str(form.get("effort_hours")).strip(),
    }
    if not task["project_id"]:
        context = _task_form_context(
            request, db, task, "/admin/projects/tasks", "Project is required."
        )
        return templates.TemplateResponse("admin/projects/project_task_form.html", context)
    if not task["title"]:
        context = _task_form_context(
            request, db, task, "/admin/projects/tasks", "Title is required."
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
    if task["assigned_to_person_id"]:
        payload_data["assigned_to_person_id"] = task["assigned_to_person_id"]
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
    from app.web.admin import get_sidebar_stats, get_current_user
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
    from app.web.admin import get_sidebar_stats, get_current_user
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


@router.get("/templates", response_class=HTMLResponse)
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
    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/projects/project_templates.html",
        {
            "request": request,
            "templates": template_items,
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/templates/new", response_class=HTMLResponse)
def project_template_new(request: Request, db: Session = Depends(get_db)):
    template = {
        "name": "",
        "project_type": "",
        "description": "",
        "is_active": True,
    }
    context = _template_form_context(request, db, template, "/admin/projects/templates")
    return templates.TemplateResponse("admin/projects/project_template_form.html", context)


@router.post("/templates", response_class=HTMLResponse)
async def project_template_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    template = {
        "name": _form_str(form.get("name")).strip(),
        "project_type": _form_str(form.get("project_type")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "is_active": form.get("is_active") == "true",
    }
    if not template["name"]:
        context = _template_form_context(
            request, db, template, "/admin/projects/templates", "Name is required."
        )
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


@router.get("/templates/{template_id}", response_class=HTMLResponse)
def project_template_detail(request: Request, template_id: str, db: Session = Depends(get_db)):
    try:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
    except Exception:
        from app.web.admin import get_sidebar_stats, get_current_user
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
    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/projects/project_template_detail.html",
        {
            "request": request,
            "template": template,
            "tasks": tasks,
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/templates/{template_id}/edit", response_class=HTMLResponse)
def project_template_edit(request: Request, template_id: str, db: Session = Depends(get_db)):
    try:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
    except Exception:
        from app.web.admin import get_sidebar_stats, get_current_user
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
    context = _template_form_context(
        request, db, template_data, f"/admin/projects/templates/{template_id}/edit"
    )
    return templates.TemplateResponse("admin/projects/project_template_form.html", context)


@router.post("/templates/{template_id}/edit", response_class=HTMLResponse)
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


@router.post("/templates/{template_id}/delete", response_class=HTMLResponse)
def project_template_delete(request: Request, template_id: str, db: Session = Depends(get_db)):
    projects_service.project_templates.delete(db=db, template_id=template_id)
    return RedirectResponse("/admin/projects/templates", status_code=303)


@router.get("/templates/{template_id}/tasks/new", response_class=HTMLResponse)
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


@router.post("/templates/{template_id}/tasks", response_class=HTMLResponse)
async def project_template_task_create(request: Request, template_id: str, db: Session = Depends(get_db)):
    form = await request.form()
    task = {
        "title": _form_str(form.get("title")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "sort_order": _form_str(form.get("sort_order")).strip(),
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
            return templates.TemplateResponse(
                "admin/projects/project_template_task_form.html", context
            )
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


@router.get("/templates/{template_id}/tasks/{task_id}/edit", response_class=HTMLResponse)
def project_template_task_edit(
    request: Request, template_id: str, task_id: str, db: Session = Depends(get_db)
):
    try:
        template = projects_service.project_templates.get(db=db, template_id=template_id)
        task = projects_service.project_template_tasks.get(db=db, task_id=task_id)
        if str(task.template_id) != template_id:
            raise ValueError("Template mismatch")
    except Exception:
        from app.web.admin import get_sidebar_stats, get_current_user
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
    }
    context = _template_task_form_context(
        request,
        db,
        {"id": str(template.id), "name": template.name},
        task_data,
        f"/admin/projects/templates/{template_id}/tasks/{task_id}/edit",
    )
    return templates.TemplateResponse("admin/projects/project_template_task_form.html", context)


@router.post("/templates/{template_id}/tasks/{task_id}/edit", response_class=HTMLResponse)
async def project_template_task_update(
    request: Request, template_id: str, task_id: str, db: Session = Depends(get_db)
):
    form = await request.form()
    try:
        existing_task = projects_service.project_template_tasks.get(db=db, task_id=task_id)
        if str(existing_task.template_id) != template_id:
            raise ValueError("Template mismatch")
    except Exception:
        from app.web.admin import get_sidebar_stats, get_current_user
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
            return templates.TemplateResponse(
                "admin/projects/project_template_task_form.html", context
            )
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


@router.post("/templates/{template_id}/tasks/{task_id}/delete", response_class=HTMLResponse)
def project_template_task_delete(
    request: Request, template_id: str, task_id: str, db: Session = Depends(get_db)
):
    try:
        existing_task = projects_service.project_template_tasks.get(db=db, task_id=task_id)
        if str(existing_task.template_id) != template_id:
            raise ValueError("Template mismatch")
    except Exception:
        from app.web.admin import get_sidebar_stats, get_current_user
        context = {
            "request": request,
            "message": "Project template task not found",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        }
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)
    projects_service.project_template_tasks.delete(db=db, task_id=task_id)
    return RedirectResponse(f"/admin/projects/templates/{template_id}", status_code=303)


@router.get("/{project_id}", response_class=HTMLResponse)
def project_detail(request: Request, project_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user
    try:
        project = projects_service.projects.get(db=db, project_id=project_id)
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
        project_id=project_id,
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
        entity_id=str(project_id),
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
        project_id=project_id,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    installation_projects = vendor_service.installation_projects.list(
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
    assigned_vendor = None
    if installation_projects and installation_projects[0].assigned_vendor_id:
        try:
            assigned_vendor = vendor_service.vendors.get(
                db=db, vendor_id=str(installation_projects[0].assigned_vendor_id)
            )
        except Exception:
            assigned_vendor = None
    return templates.TemplateResponse(
        "admin/projects/project_detail.html",
        {
            "request": request,
            "project": project,
            "tasks": tasks,
            "comments": comments,
            "activities": activities,
            "assigned_vendor": assigned_vendor,
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/{project_id}/comments", response_class=HTMLResponse)
async def project_comment_create(
    request: Request, project_id: str, db: Session = Depends(get_db)
):
    from app.web.admin import get_current_user, get_sidebar_stats
    form = await request.form()
    body = _form_str(form.get("body")).strip()
    attachments = [item for item in form.getlist("attachments") if isinstance(item, UploadFile)]
    if not body:
        return RedirectResponse(f"/admin/projects/{project_id}", status_code=303)
    prepared_attachments: list[dict] = []
    try:
        from app.services import ticket_attachments as ticket_attachment_service

        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        current_user = get_current_user(request)
        payload = ProjectCommentCreate.model_validate(
            {
                "project_id": project_id,
                "author_person_id": current_user.get("person_id") or None,
                "body": body,
                "attachments": saved_attachments or None,
            }
        )
        projects_service.project_comments.create(db=db, payload=payload)
        _log_activity(
            db=db,
            request=request,
            action="comment",
            entity_type="project",
            entity_id=str(project_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
        )
        return RedirectResponse(f"/admin/projects/{project_id}", status_code=303)
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


def _get_project_labels(db: Session, project, assigned_vendor_id: str | None = None) -> dict:
    """Build labels for project typeahead pre-population."""
    labels: dict[str, str | None] = {
        "subscriber_label": None,
        "assigned_vendor_label": None,
        "owner_label": None,
        "manager_label": None,
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
    if assigned_vendor_id:
        try:
            vendor = vendor_service.vendors.get(db=db, vendor_id=assigned_vendor_id)
            labels["assigned_vendor_label"] = vendor.name
        except Exception:
            pass
    return labels


@router.get("/{project_id}/edit", response_class=HTMLResponse)
def project_edit(request: Request, project_id: str, db: Session = Depends(get_db)):
    try:
        project = projects_service.projects.get(db=db, project_id=project_id)
    except Exception:
        from app.web.admin import get_sidebar_stats, get_current_user
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
        "project_type": project.project_type.value if project.project_type else "",
        "project_template_id": str(project.project_template_id) if project.project_template_id else "",
        "assigned_vendor_id": "",
        "status": project.status.value if project.status else ProjectStatus.planned.value,
        "priority": project.priority.value if project.priority else ProjectPriority.normal.value,
        "subscriber_id": str(project.subscriber_id) if project.subscriber_id else "",
        "owner_person_id": str(project.owner_person_id) if project.owner_person_id else "",
        "manager_person_id": str(project.manager_person_id) if project.manager_person_id else "",
        "start_at": _fmt_dt(project.start_at),
        "due_at": _fmt_dt(project.due_at),
        "is_active": bool(project.is_active),
    }
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
    assigned_vendor_id = None
    if install_projects and install_projects[0].assigned_vendor_id:
        assigned_vendor_id = str(install_projects[0].assigned_vendor_id)
        project_data["assigned_vendor_id"] = assigned_vendor_id

    labels = _get_project_labels(db, project, assigned_vendor_id)
    context = _project_form_context(
        request, db, project_data, f"/admin/projects/{project_id}/edit", labels=labels
    )
    return templates.TemplateResponse("admin/projects/project_form.html", context)


@router.post("/{project_id}/edit", response_class=HTMLResponse)
async def project_update(request: Request, project_id: str, db: Session = Depends(get_db)):
    form = await request.form()
    from app.web.admin import get_current_user
    current_user = get_current_user(request)
    project = {
        "id": project_id,
        "name": _form_str(form.get("name")).strip(),
        "code": _form_str(form.get("code")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "project_type": _form_str(form.get("project_type")).strip(),
        "project_template_id": _form_str(form.get("project_template_id")).strip(),
        "assigned_vendor_id": _form_str(form.get("assigned_vendor_id")).strip(),
        "status": _form_str(form.get("status")).strip(),
        "priority": _form_str(form.get("priority")).strip(),
        "subscriber_id": _form_str(form.get("subscriber_id")).strip(),
        "owner_person_id": _form_str(form.get("owner_person_id")).strip(),
        "manager_person_id": _form_str(form.get("manager_person_id")).strip(),
        "start_at": _form_str(form.get("start_at")).strip(),
        "due_at": _form_str(form.get("due_at")).strip(),
        "is_active": form.get("is_active") == "true",
    }
    if not project["name"]:
        context = _project_form_context(
            request, db, project, f"/admin/projects/{project_id}/edit", "Name is required."
        )
        return templates.TemplateResponse("admin/projects/project_form.html", context)

    payload_data = {
        "name": project["name"],
        "status": project["status"] or ProjectStatus.planned.value,
        "priority": project["priority"] or ProjectPriority.normal.value,
        "is_active": project["is_active"],
    }
    if project["code"]:
        payload_data["code"] = project["code"]
    if project["description"]:
        payload_data["description"] = project["description"]
    if project["project_type"]:
        payload_data["project_type"] = project["project_type"]
    payload_data["project_template_id"] = project["project_template_id"] or None
    if project["subscriber_id"]:
        payload_data["subscriber_id"] = project["subscriber_id"]
    if project["owner_person_id"]:
        payload_data["owner_person_id"] = project["owner_person_id"]
    if project["manager_person_id"]:
        payload_data["manager_person_id"] = project["manager_person_id"]
    if project["start_at"]:
        payload_data["start_at"] = project["start_at"]
    if project["due_at"]:
        payload_data["due_at"] = project["due_at"]

    try:
        payload = ProjectUpdate.model_validate(payload_data)
        before = projects_service.projects.get(db=db, project_id=project_id)
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
        if project["assigned_vendor_id"]:
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
                        "assigned_vendor_id": project["assigned_vendor_id"],
                        "subscriber_id": project["subscriber_id"] or None,
                    }
                )
                vendor_service.installation_projects.create(db=db, payload=install_payload)
        return RedirectResponse(f"/admin/projects/{project_id}", status_code=303)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _project_form_context(
            request,
            db,
            project,
            f"/admin/projects/{project_id}/edit",
            error or "Please correct the highlighted fields.",
        )
        return templates.TemplateResponse("admin/projects/project_form.html", context)


@router.post("/{project_id}/delete", response_class=HTMLResponse)
def project_delete(request: Request, project_id: str, db: Session = Depends(get_db)):
    projects_service.projects.delete(db=db, project_id=project_id)
    return RedirectResponse("/admin/projects", status_code=303)


@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def project_task_detail(request: Request, task_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user
    try:
        task = projects_service.project_tasks.get(db=db, task_id=task_id)
        project = projects_service.projects.get(db=db, project_id=str(task.project_id))
        comments = projects_service.project_task_comments.list(
            db=db,
            task_id=task_id,
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
            entity_id=str(task_id),
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
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/tasks/{task_id}/comments", response_class=HTMLResponse)
async def project_task_comment_create(request: Request, task_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user, get_sidebar_stats
    form = await request.form()
    body = _form_str(form.get("body")).strip()
    attachments = [item for item in form.getlist("attachments") if isinstance(item, UploadFile)]
    if not body:
        return RedirectResponse(f"/admin/projects/tasks/{task_id}", status_code=303)
    prepared_attachments: list[dict] = []
    try:
        from app.services import ticket_attachments as ticket_attachment_service

        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(attachments)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        current_user = get_current_user(request)
        payload = ProjectTaskCommentCreate.model_validate(
            {
                "task_id": task_id,
                "author_person_id": current_user.get("person_id") or None,
                "body": body,
                "attachments": saved_attachments or None,
            }
        )
        projects_service.project_task_comments.create(db=db, payload=payload)
        _log_activity(
            db=db,
            request=request,
            action="comment",
            entity_type="project_task",
            entity_id=str(task_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
        )
        return RedirectResponse(f"/admin/projects/tasks/{task_id}", status_code=303)
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


@router.get("/tasks/{task_id}/edit", response_class=HTMLResponse)
def project_task_edit(request: Request, task_id: str, db: Session = Depends(get_db)):
    try:
        task = projects_service.project_tasks.get(db=db, task_id=task_id)
    except Exception:
        from app.web.admin import get_sidebar_stats, get_current_user
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
        "created_by_person_id": str(task.created_by_person_id) if task.created_by_person_id else "",
        "start_at": _fmt_dt(task.start_at),
        "due_at": _fmt_dt(task.due_at),
        "effort_hours": str(task.effort_hours) if task.effort_hours is not None else "",
    }
    context = _task_form_context(
        request, db, task_data, f"/admin/projects/tasks/{task_id}/edit"
    )
    return templates.TemplateResponse("admin/projects/project_task_form.html", context)


@router.post("/tasks/{task_id}/edit", response_class=HTMLResponse)
async def project_task_update(request: Request, task_id: str, db: Session = Depends(get_db)):
    form = await request.form()
    from app.web.admin import get_current_user
    current_user = get_current_user(request)
    task = {
        "id": task_id,
        "project_id": _form_str(form.get("project_id")).strip(),
        "title": _form_str(form.get("title")).strip(),
        "description": _form_str(form.get("description")).strip(),
        "status": _form_str(form.get("status")).strip(),
        "priority": _form_str(form.get("priority")).strip(),
        "assigned_to_person_id": _form_str(form.get("assigned_to_person_id")).strip(),
        "created_by_person_id": _form_str(form.get("created_by_person_id")).strip(),
        "start_at": _form_str(form.get("start_at")).strip(),
        "due_at": _form_str(form.get("due_at")).strip(),
        "effort_hours": _form_str(form.get("effort_hours")).strip(),
    }
    if not task["project_id"]:
        context = _task_form_context(
            request, db, task, f"/admin/projects/tasks/{task_id}/edit", "Project is required."
        )
        return templates.TemplateResponse("admin/projects/project_task_form.html", context)
    if not task["title"]:
        context = _task_form_context(
            request, db, task, f"/admin/projects/tasks/{task_id}/edit", "Title is required."
        )
        return templates.TemplateResponse("admin/projects/project_task_form.html", context)

    payload_data = {
        "project_id": task["project_id"],
        "title": task["title"],
        "status": task["status"] or TaskStatus.todo.value,
        "priority": task["priority"] or TaskPriority.normal.value,
    }
    if task["description"]:
        payload_data["description"] = task["description"]
    if task["assigned_to_person_id"]:
        payload_data["assigned_to_person_id"] = task["assigned_to_person_id"]
    if task["created_by_person_id"]:
        payload_data["created_by_person_id"] = task["created_by_person_id"]
    if task["start_at"]:
        payload_data["start_at"] = task["start_at"]
    if task["due_at"]:
        payload_data["due_at"] = task["due_at"]
    if task["effort_hours"]:
        payload_data["effort_hours"] = task["effort_hours"]

    try:
        payload = ProjectTaskUpdate.model_validate(payload_data)
        before = projects_service.project_tasks.get(db=db, task_id=task_id)
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
        return RedirectResponse(f"/admin/projects/tasks/{task_id}", status_code=303)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _task_form_context(
            request,
            db,
            task,
            f"/admin/projects/tasks/{task_id}/edit",
            error or "Please correct the highlighted fields.",
        )
        return templates.TemplateResponse("admin/projects/project_task_form.html", context)


@router.post("/tasks/{task_id}/delete", response_class=HTMLResponse)
def project_task_delete(request: Request, task_id: str, db: Session = Depends(get_db)):
    projects_service.project_tasks.delete(db=db, task_id=task_id)
    return RedirectResponse("/admin/projects/tasks", status_code=303)
