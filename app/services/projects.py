import html
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, ClassVar
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.crm.sales import Lead
from app.models.domain_settings import SettingDomain
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import Person
from app.models.projects import (
    Project,
    ProjectComment,
    ProjectPriority,
    ProjectStatus,
    ProjectTask,
    ProjectTaskAssignee,
    ProjectTaskComment,
    ProjectTaskDependency,
    ProjectTemplate,
    ProjectTemplateTask,
    ProjectTemplateTaskDependency,
    ProjectType,
    TaskPriority,
    TaskStatus,
)
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket
from app.models.workflow import SlaClock, SlaClockStatus, SlaPolicy, WorkflowEntityType
from app.models.workforce import WorkOrder
from app.schemas.projects import (
    ProjectCommentCreate,
    ProjectCommentUpdate,
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
from app.services import settings_spec
from app.services.branding import get_branding
from app.services.common import (
    apply_ordering,
    apply_pagination,
    coerce_uuid,
    ensure_exists,
    validate_enum,
)
from app.services.events import emit_event
from app.services.events.types import EventType
from app.services.numbering import generate_number
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)

FIBER_INSTALLATION_STAGE_ORDER: tuple[str, ...] = (
    "project_plan",
    "project_survey",
    "drop_cable_installation",
    "survey_approval_po_issuance",
    "last_mile_installation",
    "power_splicing_activation",
)

FIBER_INSTALLATION_STAGE_TITLES: dict[str, str] = {
    "project_plan": "Project Plan",
    "project_survey": "Project Survey",
    "drop_cable_installation": "Drop Cable Installation",
    "survey_approval_po_issuance": "Survey Approval & PO Issuance",
    "last_mile_installation": "Last Mile Installation",
    "power_splicing_activation": "Power Direction, Splicing & Customer Activation",
}

FIBER_PROJECT_TASK_SLA_POLICY_NAME = "Fiber Project Task SLA"


def _normalize_title(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().replace("-", " ").replace("_", " ").split())


def _resolve_customer_email(db: Session, project: Project) -> str | None:
    if project.subscriber and project.subscriber.person and isinstance(project.subscriber.person.email, str):
        email = project.subscriber.person.email.strip()
        if email:
            return email
    if project.subscriber_id:
        subscriber = db.get(Subscriber, project.subscriber_id)
        if subscriber and subscriber.person_id:
            person = db.get(Person, subscriber.person_id)
            if person and isinstance(person.email, str):
                email = person.email.strip()
                if email:
                    return email
    if project.lead and project.lead.person and isinstance(project.lead.person.email, str):
        email = project.lead.person.email.strip()
        if email:
            return email
    if project.lead_id:
        lead = db.get(Lead, project.lead_id)
        if lead and lead.person and isinstance(lead.person.email, str):
            email = lead.person.email.strip()
            if email:
                return email
    return None


def _resolve_customer_name(project: Project) -> str:
    if project.subscriber and project.subscriber.person:
        person = project.subscriber.person
        if isinstance(person.display_name, str) and person.display_name.strip():
            return person.display_name.strip()
        if isinstance(person.first_name, str) and isinstance(person.last_name, str):
            full_name = f"{person.first_name} {person.last_name}".strip()
            if full_name:
                return full_name
        if isinstance(person.email, str) and person.email.strip():
            return person.email.strip()
    if project.lead and project.lead.person:
        person = project.lead.person
        if isinstance(person.display_name, str) and person.display_name.strip():
            return person.display_name.strip()
        if isinstance(person.first_name, str) and isinstance(person.last_name, str):
            full_name = f"{person.first_name} {person.last_name}".strip()
            if full_name:
                return full_name
        if isinstance(person.email, str) and person.email.strip():
            return person.email.strip()
    return "Customer"


def _resolve_fiber_stage_key(task: ProjectTask) -> str | None:
    metadata = task.metadata_ if isinstance(task.metadata_, dict) else {}
    raw_stage = metadata.get("fiber_stage_key")
    if isinstance(raw_stage, str) and raw_stage in FIBER_INSTALLATION_STAGE_ORDER:
        return raw_stage

    normalized = _normalize_title(task.title)
    if "project plan" in normalized:
        return "project_plan"
    if "project survey" in normalized:
        return "project_survey"
    if "drop cable" in normalized:
        return "drop_cable_installation"
    if ("po" in normalized and "issuance" in normalized) or ("survey approval" in normalized):
        return "survey_approval_po_issuance"
    if "last mile" in normalized:
        return "last_mile_installation"
    if "splicing" in normalized or "activation" in normalized or "power direction" in normalized:
        return "power_splicing_activation"
    return None


def _fiber_stage_task(db: Session, project_id: UUID, stage_key: str) -> ProjectTask | None:
    candidates = (
        db.query(ProjectTask)
        .filter(ProjectTask.project_id == project_id, ProjectTask.is_active.is_(True))
        .order_by(ProjectTask.created_at.asc())
        .all()
    )
    for candidate in candidates:
        if _resolve_fiber_stage_key(candidate) == stage_key:
            return candidate
    return None


def _fiber_stage_anchor(task: ProjectTask | None, fallback: datetime) -> datetime:
    if not task:
        return fallback
    return task.completed_at or task.created_at or fallback


def _compute_fiber_stage_due_at(db: Session, project: Project, task: ProjectTask, stage_key: str) -> datetime:
    baseline = project.created_at or datetime.now(UTC)
    if stage_key == "project_plan":
        return baseline + timedelta(hours=24)
    if stage_key == "project_survey":
        plan = _fiber_stage_task(db, project.id, "project_plan")
        return _fiber_stage_anchor(plan, baseline) + timedelta(hours=24)
    if stage_key == "drop_cable_installation":
        survey = _fiber_stage_task(db, project.id, "project_survey")
        return _fiber_stage_anchor(survey, baseline) + timedelta(hours=48)
    if stage_key == "survey_approval_po_issuance":
        survey = _fiber_stage_task(db, project.id, "project_survey")
        return _fiber_stage_anchor(survey, baseline) + timedelta(hours=24)
    if stage_key == "last_mile_installation":
        survey = _fiber_stage_task(db, project.id, "project_survey")
        return _fiber_stage_anchor(survey, baseline) + timedelta(days=5)
    if stage_key == "power_splicing_activation":
        drop_task = _fiber_stage_task(db, project.id, "drop_cable_installation")
        last_mile_task = _fiber_stage_task(db, project.id, "last_mile_installation")
        drop_anchor = _fiber_stage_anchor(drop_task, baseline)
        last_mile_anchor = _fiber_stage_anchor(last_mile_task, baseline)
        return max(drop_anchor, last_mile_anchor) + timedelta(hours=24)
    return (task.created_at or baseline) + timedelta(hours=24)


def _ensure_project_task_sla_policy(db: Session) -> SlaPolicy:
    policy = (
        db.query(SlaPolicy)
        .filter(SlaPolicy.entity_type == WorkflowEntityType.project_task)
        .filter(SlaPolicy.name == FIBER_PROJECT_TASK_SLA_POLICY_NAME)
        .filter(SlaPolicy.is_active.is_(True))
        .first()
    )
    if policy:
        return policy
    policy = SlaPolicy(
        name=FIBER_PROJECT_TASK_SLA_POLICY_NAME,
        entity_type=WorkflowEntityType.project_task,
        description="SLA policy for fiber installation project stages",
        is_active=True,
    )
    db.add(policy)
    db.flush()
    return policy


def _latest_task_sla_clock(db: Session, task_id: UUID) -> SlaClock | None:
    return (
        db.query(SlaClock)
        .filter(
            SlaClock.entity_type == WorkflowEntityType.project_task,
            SlaClock.entity_id == task_id,
        )
        .order_by(SlaClock.created_at.desc())
        .first()
    )


def _sync_task_sla_clock(db: Session, task: ProjectTask) -> None:
    if not task.due_at:
        return
    policy = _ensure_project_task_sla_policy(db)
    clock = _latest_task_sla_clock(db, task.id)
    now = datetime.now(UTC)
    terminal = {TaskStatus.done, TaskStatus.canceled}

    if task.status in terminal:
        if clock and clock.status != SlaClockStatus.completed:
            clock.status = SlaClockStatus.completed
            clock.completed_at = task.completed_at or now
        return

    if not clock or clock.status == SlaClockStatus.completed:
        db.add(
            SlaClock(
                policy_id=policy.id,
                entity_type=WorkflowEntityType.project_task,
                entity_id=task.id,
                priority=task.priority.value if task.priority else None,
                status=SlaClockStatus.running,
                started_at=task.created_at or now,
                due_at=task.due_at,
            )
        )
        return

    if clock.status in {SlaClockStatus.paused, SlaClockStatus.breached}:
        clock.status = SlaClockStatus.running
    clock.priority = task.priority.value if task.priority else None
    clock.due_at = task.due_at


def _apply_fiber_stage_defaults(db: Session, task: ProjectTask) -> None:
    project = db.get(Project, task.project_id)
    if not project or project.project_type != ProjectType.fiber_optics_installation:
        return

    stage_key = _resolve_fiber_stage_key(task)
    if not stage_key:
        return

    metadata = dict(task.metadata_) if isinstance(task.metadata_, dict) else {}
    metadata["fiber_stage_key"] = stage_key
    metadata.setdefault("fiber_stage_title", FIBER_INSTALLATION_STAGE_TITLES.get(stage_key, task.title))
    metadata["fiber_sla_managed"] = True
    task.metadata_ = metadata
    task.due_at = _compute_fiber_stage_due_at(db, project, task, stage_key)


def _queue_in_app_notification(db: Session, recipient: str, subject: str, body: str) -> None:
    now = datetime.now(UTC)
    db.add(
        Notification(
            channel=NotificationChannel.push,
            recipient=recipient,
            subject=subject,
            body=body,
            status=NotificationStatus.delivered,
            sent_at=now,
        )
    )


def _queue_email_notification(db: Session, recipient: str, subject: str, body: str) -> None:
    db.add(
        Notification(
            channel=NotificationChannel.email,
            recipient=recipient,
            subject=subject,
            body=body,
            status=NotificationStatus.queued,
        )
    )


def _next_fiber_stage_label(task: ProjectTask) -> str | None:
    stage_key = _resolve_fiber_stage_key(task)
    if not stage_key or stage_key not in FIBER_INSTALLATION_STAGE_ORDER:
        return None
    index = FIBER_INSTALLATION_STAGE_ORDER.index(stage_key)
    if index >= len(FIBER_INSTALLATION_STAGE_ORDER) - 1:
        return None
    next_key = FIBER_INSTALLATION_STAGE_ORDER[index + 1]
    return FIBER_INSTALLATION_STAGE_TITLES.get(next_key, next_key.replace("_", " ").title())


def _next_template_task_label(db: Session, project: Project, task: ProjectTask) -> str | None:
    if not project.project_template_id or not task.template_task_id:
        return None

    template_tasks = (
        db.query(ProjectTemplateTask)
        .filter(ProjectTemplateTask.template_id == project.project_template_id)
        .filter(ProjectTemplateTask.is_active.is_(True))
        .order_by(ProjectTemplateTask.sort_order.asc(), ProjectTemplateTask.created_at.asc())
        .all()
    )
    if not template_tasks:
        return None

    current_index = None
    for index, template_task in enumerate(template_tasks):
        if template_task.id == task.template_task_id:
            current_index = index
            break
    if current_index is None:
        return None

    project_tasks = (
        db.query(ProjectTask).filter(ProjectTask.project_id == project.id).filter(ProjectTask.is_active.is_(True)).all()
    )
    project_tasks_by_template_id = {
        project_task.template_task_id: project_task for project_task in project_tasks if project_task.template_task_id
    }

    for template_task in template_tasks[current_index + 1 :]:
        mapped_task = project_tasks_by_template_id.get(template_task.id)
        if mapped_task and mapped_task.status in {TaskStatus.done, TaskStatus.canceled}:
            continue
        return mapped_task.title if mapped_task else template_task.title
    return None


def _notify_customer_task_completed(db: Session, project: Project, task: ProjectTask) -> None:
    recipient = _resolve_customer_email(db, project)
    if not recipient:
        return
    customer_name = _resolve_customer_name(project)
    next_stage = _next_template_task_label(db, project, task) or _next_fiber_stage_label(task)
    subject = "Project Update - Stage Completed"
    project_ref = project.number or str(project.id)
    branding = get_branding(db)
    company = html.escape(branding.get("company_name", "Dotmac Technologies"))
    logo_url = branding.get("logo_url") or "https://erp.dotmac.ng/files/dotmac%20no%20bg.png"
    customer_label = html.escape(customer_name)
    project_name = html.escape(project.name or "Project")
    project_code = html.escape(project_ref)
    completed_stage = html.escape(task.title or "Project Task")
    next_stage_html = html.escape(next_stage) if next_stage else ""
    logo_url_html = html.escape(logo_url)

    next_stage_block = ""
    if next_stage_html:
        next_stage_block = (
            '<div style="background-color: #ffffff; border: 1px solid #dbeafe; border-radius: 8px; '
            'padding: 14px 16px; margin: 12px 0 18px;">'
            '<p style="margin: 0; font-size: 15px; color: #0f172a;"><strong>&#128279; Next Stage</strong></p>'
            f'<p style="margin: 6px 0 0; font-size: 16px; color: #111827;">{next_stage_html}</p>'
            "</div>"
        )

    body = (
        "<div style=\"font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; "
        "line-height: 1.8; color: #333; background-color: #f4f4f9; padding: 25px; "
        "border: 1px solid #ccc; border-radius: 10px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); "
        'position: relative;">'
        '<div style="position: absolute; top: 14px; right: 14px;">'
        f'<img src="{logo_url_html}" alt="Dotmac Logo" style="max-width: 150px; height: auto;">'
        "</div>"
        '<div style="text-align: center; margin-bottom: 20px;">'
        '<h1 style="color: green; font-size: 24px; margin: 0;">Project Stage Completed</h1>'
        "</div>"
        f'<p style="font-size: 16px; color: #0f172a; margin-top: 20px;">Dear {customer_label},</p>'
        '<p style="font-size: 15px; color: #555; margin: 15px 0;">'
        "We are pleased to inform you that your project "
        f"<strong>{project_name}</strong> ({project_code}) has successfully completed the "
        f"<strong>{completed_stage}</strong> stage."
        "</p>"
        '<div style="background-color: #ffffff; border: 1px solid #dcfce7; border-radius: 8px; '
        'padding: 14px 16px; margin: 12px 0 18px;">'
        '<p style="margin: 0; font-size: 15px; color: #14532d;"><strong>&#9989; Completed Stage</strong></p>'
        f'<p style="margin: 6px 0 0; font-size: 16px; color: #111827;">{completed_stage}</p>'
        "</div>"
        f"{next_stage_block}"
        '<p style="font-size: 15px; color: #555; margin: 10px 0;">'
        "Our technical team is progressing steadily to ensure a smooth and timely completion of your installation."
        "</p>"
        '<p style="font-size: 15px; color: #555; margin: 10px 0 18px;">'
        "We will continue to keep you informed at every key milestone."
        "</p>"
        '<p style="font-size: 15px; color: #555; margin: 10px 0;">'
        f"Thank you for choosing {company}."
        "</p>"
        '<p style="font-size: 15px; color: #0f172a; margin: 10px 0 0;">'
        "Warm regards,<br>The Dotmac Team."
        "</p>"
        "</div>"
    )
    _queue_email_notification(db, recipient, subject, body)


def _notify_customer_project_completed(db: Session, project: Project) -> None:
    recipient = _resolve_customer_email(db, project)
    if not recipient:
        return
    project_ref = project.number or str(project.id)
    subject = f"Project completed: {project.name}"
    body = (
        f"Your installation project '{project.name}' ({project_ref}) is now completed.\n"
        "Please reply to this email to confirm your satisfaction with the service."
    )
    _queue_email_notification(db, recipient, subject, body)


def notify_project_task_sla_breach(db: Session, clock: SlaClock) -> None:
    if clock.entity_type != WorkflowEntityType.project_task:
        return
    task = db.get(ProjectTask, clock.entity_id)
    if not task:
        return
    project = db.get(Project, task.project_id)
    if not project:
        return

    metadata = dict(task.metadata_) if isinstance(task.metadata_, dict) else {}
    metadata["sla_breached"] = True
    metadata["sla_breached_at"] = (clock.breached_at or datetime.now(UTC)).isoformat()
    task.metadata_ = metadata

    role_person_ids = [
        project.project_manager_person_id,
        project.assistant_manager_person_id,
        project.manager_person_id,
    ]
    person_ids = [person_id for person_id in role_person_ids if person_id]
    if not person_ids:
        return

    people = db.query(Person).filter(Person.id.in_(person_ids)).all()
    recipients = {person.email.strip() for person in people if isinstance(person.email, str) and person.email.strip()}
    if not recipients:
        return

    task_ref = task.number or str(task.id)
    project_ref = project.number or str(project.id)
    subject = f"SLA breach: {task.title}"
    body = (
        f"Task {task_ref} in project {project_ref} breached its SLA timeline.\n"
        "Action required by PM / Assistant PM / SPC. PM supervisor has been tagged."
    )
    for recipient in recipients:
        _queue_in_app_notification(db, recipient, subject, body)
        _queue_email_notification(db, recipient, subject, body)


def _seed_fiber_installation_tasks(db: Session, project: Project) -> None:
    if project.project_type != ProjectType.fiber_optics_installation:
        return
    existing = (
        db.query(ProjectTask).filter(ProjectTask.project_id == project.id, ProjectTask.is_active.is_(True)).first()
    )
    if existing:
        return

    stage_offsets = {
        "project_plan": timedelta(hours=24),
        "project_survey": timedelta(hours=48),
        "drop_cable_installation": timedelta(hours=96),
        "survey_approval_po_issuance": timedelta(hours=72),
        "last_mile_installation": timedelta(days=7),
        "power_splicing_activation": timedelta(days=8),
    }
    baseline = project.created_at or datetime.now(UTC)

    for stage_key in FIBER_INSTALLATION_STAGE_ORDER:
        number = generate_number(
            db=db,
            domain=SettingDomain.numbering,
            sequence_key="project_task_number",
            enabled_key="project_task_number_enabled",
            prefix_key="project_task_number_prefix",
            padding_key="project_task_number_padding",
            start_key="project_task_number_start",
        )
        task = ProjectTask(
            project_id=project.id,
            title=FIBER_INSTALLATION_STAGE_TITLES[stage_key],
            status=TaskStatus.todo,
            priority=TaskPriority.normal,
            created_by_person_id=project.created_by_person_id,
            due_at=baseline + stage_offsets[stage_key],
            metadata_={
                "fiber_stage_key": stage_key,
                "fiber_stage_title": FIBER_INSTALLATION_STAGE_TITLES[stage_key],
                "fiber_sla_managed": True,
            },
        )
        if number:
            task.number = number
        db.add(task)
        db.flush()
        _sync_task_sla_clock(db, task)


def _notify_project_roles_created_in_app(db: Session, project: Project) -> None:
    """Create in-app notifications for internal roles on project creation.

    We store these as Notification rows with a non-email channel so the email queue
    does not attempt delivery. Admin UI shows Notification rows in the dropdown
    filtered by recipient (email/person_id/user_id).
    """
    role_specs: list[tuple[str, str]] = [
        ("project_manager_person_id", "Project Manager"),
        ("assistant_manager_person_id", "Site Project Coordinator"),
    ]

    roles_by_person_id: dict[UUID, list[str]] = {}
    person_ids: list[UUID] = []
    for attr, label in role_specs:
        person_id = getattr(project, attr, None)
        if not person_id:
            continue
        if person_id not in roles_by_person_id:
            roles_by_person_id[person_id] = []
            person_ids.append(person_id)
        if label not in roles_by_person_id[person_id]:
            roles_by_person_id[person_id].append(label)

    if not person_ids:
        return

    people = db.query(Person).filter(Person.id.in_(person_ids)).all()
    people_by_id = {p.id: p for p in people}

    # Use APP_URL (or DomainSetting notification/app_url) so links work across hosts.
    from app.services import email as email_service

    base_url = (email_service.get_app_url(db) or "").rstrip("/")
    project_ref = project.number or str(project.id)
    project_url = f"{base_url}/admin/projects/{project_ref}" if base_url else f"/admin/projects/{project_ref}"

    site = (project.customer_address or project.region or "").strip()

    subject = f"New Project Assignment: {project.name}"
    now = datetime.now(UTC)

    # De-dupe by recipient email so one person with multiple roles gets one notification.
    created_for: set[str] = set()
    for person_id, roles in roles_by_person_id.items():
        person = people_by_id.get(person_id)
        if not person or not isinstance(person.email, str) or not person.email.strip():
            continue
        recipient = person.email.strip()
        if recipient in created_for:
            continue
        created_for.add(recipient)

        roles_label = ", ".join(roles)
        body_lines = [f"You have been assigned as {roles_label} for this project."]
        if site:
            body_lines.append(f"Site: {site}.")
        body_lines.append(f"Open: {project_url}")

        db.add(
            Notification(
                channel=NotificationChannel.push,
                recipient=recipient,
                subject=subject,
                body="\n".join(body_lines),
                status=NotificationStatus.delivered,
                sent_at=now,
            )
        )

    db.commit()


def _ensure_person(db: Session, person_id: str):
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")


def _ensure_project_template(db: Session, template_id: str):
    template = db.get(ProjectTemplate, coerce_uuid(template_id))
    if not template:
        raise HTTPException(status_code=404, detail="Project template not found")
    return template


def _ensure_subscriber(db: Session, subscriber_id: str):
    ensure_exists(db, Subscriber, subscriber_id, "Subscriber not found")


def _ensure_lead(db: Session, lead_id: str):
    lead = db.get(Lead, coerce_uuid(lead_id))
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")


def _normalize_assignee_ids(assignee_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in assignee_ids:
        if not raw:
            continue
        try:
            coerced = str(coerce_uuid(raw))
        except Exception:
            coerced = None
        if not coerced:
            continue
        if coerced not in seen:
            seen.add(coerced)
            normalized.append(coerced)
    return normalized


def _sync_project_task_assignees(db: Session, task: ProjectTask, assignee_ids: list[str] | None) -> None:
    if assignee_ids is None:
        return
    normalized = _normalize_assignee_ids(assignee_ids)
    for person_id in normalized:
        _ensure_person(db, person_id)

    task.assigned_to_person_id = coerce_uuid(normalized[0]) if normalized else None

    current_ids = {str(assignee.person_id) for assignee in task.assignees}
    target_ids = set(normalized)

    for person_id in target_ids - current_ids:
        task.assignees.append(ProjectTaskAssignee(task_id=task.id, person_id=coerce_uuid(person_id)))
    if target_ids != current_ids:
        for assignee in list(task.assignees):
            if str(assignee.person_id) not in target_ids:
                task.assignees.remove(assignee)


def _person_label(person: Person | None) -> str:
    if not person:
        return "Someone"
    if person.display_name:
        return person.display_name
    name = f"{person.first_name} {person.last_name}".strip()
    if name:
        return name
    return person.email


def _format_dt(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo:
        return value.strftime("%b %d, %Y %H:%M %Z")
    return value.strftime("%b %d, %Y %H:%M")


def _notify_project_task_assigned(
    db: Session,
    task: ProjectTask,
    project: Project,
    assigned_to: Person,
    created_by: Person | None,
) -> None:
    from app.services import email as email_service

    try:
        if not assigned_to.email:
            logger.warning("project_task_assigned_missing_email task_id=%s", task.id)
            return

        assignee_name = html.escape(_person_label(assigned_to))
        html.escape(_person_label(created_by)) if created_by else None
        due_label = _format_dt(task.due_at)
        start_label = _format_dt(task.start_at)

        app_url = email_service.get_app_url(db).rstrip("/")
        task_url = f"{app_url}/admin/projects/tasks/{task.id}" if app_url else None
        project_ref = project.number or str(project.id)
        project_url = f"{app_url}/admin/projects/{project_ref}" if app_url else None

        branding = get_branding(db)
        company = html.escape(branding["company_name"])
        support_email = branding.get("support_email")
        support_phone = branding.get("support_phone")
        logo_url = branding.get("logo_url")

        subject = f"New project task assigned: {task.title or 'Task'}"
        safe_title = html.escape(task.title or "Task")
        safe_project = html.escape(project.name or "Project")
        status_label = html.escape(task.status.value) if task.status else "todo"
        priority_label = html.escape(task.priority.value) if task.priority else "normal"
        description_block = ""
        if task.description:
            description_block = f"<p><strong>Description:</strong><br>{html.escape(task.description)}</p>"

        task_link_url = task_url or f"{app_url}/admin/projects/tasks"
        task_link_block = (
            '<div style="text-align: center; margin: 20px 0;">'
            f'<a href="{task_link_url}" '
            'style="background-color: #16a34a; color: #fff; text-decoration: none; '
            'padding: 12px 20px; border-radius: 6px; display: inline-block; font-weight: 600;">'
            "View Project Task"
            "</a>"
            "</div>"
        )

        project_link_block = ""
        if project_url:
            project_link_block = (
                '<div style="text-align: center; margin: 12px 0 20px;">'
                f'<a href="{project_url}" '
                'style="background-color: #0f766e; color: #fff; text-decoration: none; '
                'padding: 12px 20px; border-radius: 6px; display: inline-block; font-weight: 600;">'
                "View Project"
                "</a>"
                "</div>"
            )

        logo_block = ""
        if logo_url:
            logo_block = (
                '<div style="position: absolute; top: 15px; right: 15px;">'
                f'<img src="{html.escape(logo_url)}" '
                f'alt="{company}" style="max-width: 150px; height: auto;">'
                "</div>"
            )

        contact_block = ""
        contact_parts: list[str] = []
        if support_email:
            safe_email = html.escape(support_email)
            contact_parts.append(
                f'<a href="mailto:{safe_email}" style="color: green; text-decoration: none;">{safe_email}</a>'
            )
        if support_phone:
            safe_phone = html.escape(support_phone)
            contact_parts.append(
                f'<a href="tel:{safe_phone}" style="color: green; text-decoration: none;">{safe_phone}</a>'
            )
        if contact_parts:
            contact_block = (
                '<p style="font-size: 15px; color: #555; margin: 15px 0;">'
                "For further inquiries, contact us at " + " or ".join(contact_parts) + ".</p>"
            )

        body = (
            "<div style=\"font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.8; "
            "color: #333; background-color: #f4f4f9; padding: 25px; border: 1px solid #ccc; "
            'border-radius: 10px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); position: relative;">'
            f"{logo_block}"
            '<div style="text-align: center; margin-bottom: 20px;">'
            '<h1 style="color: green; font-size: 24px; margin: 0;">Task Assigned</h1>'
            "</div>"
            f'<p style="font-size: 16px; color: green; margin-top: 20px;">Dear {assignee_name},</p>'
            '<p style="font-size: 15px; color: #555; margin: 15px 0;">'
            "You have been assigned a new project task. Please find the details below:"
            "</p>"
            '<div style="background-color: #fff; border: 2px solid #e2e2e2; border-radius: 8px; '
            'padding: 20px; margin-bottom: 20px;">'
            f'<p style="font-size: 15px; margin: 0; line-height: 1.5;">'
            f'<strong style="color: red;">Task:</strong> <span style="color: #555;">{safe_title}</span><br>'
            f'<strong style="color: red;">Project:</strong> <span style="color: #555;">{safe_project}</span><br>'
            f'<strong style="color: red;">Status:</strong> '
            f'<span style="color: #555;">{status_label}</span><br>'
            f'<strong style="color: red;">Task ID:</strong> <span style="color: #555;">{task.id}</span><br>'
            f'<strong style="color: red;">Start:</strong> <span style="color: #555;">{start_label or "N/A"}</span><br>'
            f'<strong style="color: red;">Due:</strong> <span style="color: #555;">{due_label or "N/A"}</span><br>'
            f'<strong style="color: red;">Priority:</strong> '
            f'<span style="color: #555;">{priority_label}</span>'
            f"</p>"
            "</div>"
            f"{description_block}"
            '<p style="font-size: 15px; color: #555; margin: 15px 0;">'
            "We will keep you updated with further progress."
            "</p>"
            f"{task_link_block}"
            f"{project_link_block}"
            f"{contact_block}"
            '<p style="font-size: 15px; color: green; text-align: left; font-style: italic;">'
            f'Thank you for choosing <strong style="color: red;">{company}</strong>.'
            "</p>"
            '<p style="font-size: 15px; color: green; text-align: right; font-style: italic;">'
            "Best regards,<br>"
            f'<span style="color: red; font-weight: bold;">{company} Support Team</span>'
            "</p>"
            "</div>"
        )

        email_service.send_email(
            db=db,
            to_email=assigned_to.email,
            subject=subject,
            body_html=body,
            body_text=None,
            track=True,
        )
        db.add(
            Notification(
                channel=NotificationChannel.push,
                recipient=assigned_to.email,
                subject=subject,
                body=f"You have been assigned a project task: {task.title or 'Task'}",
                status=NotificationStatus.queued,
            )
        )
        db.flush()
    except Exception as exc:
        logger.error("project_task_assigned_notify_failed task_id=%s error=%s", task.id, exc)


class Projects(ListResponseMixin):
    PROJECT_TYPE_DURATIONS: ClassVar[dict[ProjectType, int]] = {
        ProjectType.air_fiber_installation: 3,
        ProjectType.air_fiber_relocation: 3,
        ProjectType.fiber_optics_installation: 14,
        ProjectType.fiber_optics_relocation: 14,
        ProjectType.cable_rerun: 5,
    }

    @staticmethod
    def _duration_days_for_type(project_type: ProjectType | None) -> int | None:
        if not project_type:
            return None
        return Projects.PROJECT_TYPE_DURATIONS.get(project_type)

    @staticmethod
    def _get_region_pm_assignments(db: Session, region: str | None) -> tuple[str | None, str | None]:
        """Look up PM + SPC person_id for the given region from settings."""
        if not region:
            return None, None
        region_pm_map = settings_spec.resolve_value(db, SettingDomain.projects, "region_pm_assignments")
        if not region_pm_map or not isinstance(region_pm_map, dict):
            return None, None
        entry = region_pm_map.get(region)
        pm_id: str | None = None
        spc_id: str | None = None
        if isinstance(entry, dict):
            pm_id = entry.get("manager_person_id") or entry.get("project_manager_person_id")
            spc_id = (
                entry.get("spc_person_id")
                or entry.get("assistant_person_id")
                or entry.get("assistant_manager_person_id")
            )
        elif isinstance(entry, str):
            pm_id = entry
        if pm_id:
            person = db.get(Person, coerce_uuid(pm_id))
            if not person:
                pm_id = None
            else:
                pm_id = str(person.id)
        if spc_id:
            person = db.get(Person, coerce_uuid(spc_id))
            if not person:
                spc_id = None
            else:
                spc_id = str(person.id)
        return pm_id, spc_id

    @staticmethod
    def _get_pm_for_region(db: Session, region: str | None) -> str | None:
        pm_id, _assistant_id = Projects._get_region_pm_assignments(db, region)
        return pm_id

    @staticmethod
    def list_for_site_surveys(db: Session):
        return (
            db.query(Project)
            .filter(Project.status.notin_([ProjectStatus.canceled, ProjectStatus.completed]))
            .order_by(Project.name)
            .all()
        )

    @staticmethod
    def create(db: Session, payload: ProjectCreate):
        if payload.created_by_person_id:
            _ensure_person(db, str(payload.created_by_person_id))
        if payload.owner_person_id:
            _ensure_person(db, str(payload.owner_person_id))
        if payload.manager_person_id:
            _ensure_person(db, str(payload.manager_person_id))
        if payload.subscriber_id:
            _ensure_subscriber(db, str(payload.subscriber_id))
        if payload.lead_id:
            _ensure_lead(db, str(payload.lead_id))
        if payload.project_template_id:
            _ensure_project_template(db, str(payload.project_template_id))
        data = payload.model_dump()
        number = generate_number(
            db=db,
            domain=SettingDomain.numbering,
            sequence_key="project_number",
            enabled_key="project_number_enabled",
            prefix_key="project_number_prefix",
            padding_key="project_number_padding",
            start_key="project_number_start",
        )
        if number:
            data["number"] = number
        # Auto-assign PM based on region if not already specified
        if data.get("region"):
            auto_pm, auto_spc = Projects._get_region_pm_assignments(db, data["region"])
            if auto_pm:
                if not data.get("project_manager_person_id"):
                    data["project_manager_person_id"] = coerce_uuid(auto_pm)
                if not data.get("manager_person_id"):
                    data["manager_person_id"] = coerce_uuid(auto_pm)
            if auto_spc and not data.get("assistant_manager_person_id"):
                data["assistant_manager_person_id"] = coerce_uuid(auto_spc)
        fields_set = payload.model_fields_set
        if "status" not in fields_set:
            default_status = settings_spec.resolve_value(db, SettingDomain.projects, "default_project_status")
            if default_status:
                data["status"] = validate_enum(default_status, ProjectStatus, "status")
        if "priority" not in fields_set:
            default_priority = settings_spec.resolve_value(db, SettingDomain.projects, "default_project_priority")
            if default_priority:
                data["priority"] = validate_enum(default_priority, ProjectPriority, "priority")
        if not data.get("start_at") or not data.get("due_at"):
            duration_days = Projects._duration_days_for_type(data.get("project_type"))
            if duration_days:
                start_at = data.get("start_at") or datetime.now(UTC)
                data["start_at"] = start_at
                if not data.get("due_at"):
                    data["due_at"] = start_at + timedelta(days=duration_days)
        project = Project(**data)
        db.add(project)
        db.commit()
        db.refresh(project)

        if not payload.project_template_id:
            _seed_fiber_installation_tasks(db, project)
            db.commit()
            db.refresh(project)

        customer_name = None
        if project.subscriber and project.subscriber.person:
            person = project.subscriber.person
            customer_name = person.display_name or person.email
        if not customer_name and project.lead_id:
            lead = db.get(Lead, project.lead_id)
            if lead and lead.person:
                customer_name = lead.person.display_name or lead.person.email

        # Emit project created event
        emit_event(
            db,
            EventType.project_created,
            {
                "project_id": str(project.id),
                "name": project.name,
                "status": project.status.value if project.status else None,
                "project_type": project.project_type.value if project.project_type else None,
                "region": project.region,
                "customer_name": customer_name,
            },
            project_id=project.id,
            subscriber_id=project.subscriber_id,
        )

        if payload.project_template_id:
            ProjectTemplateTasks.replace_project_tasks(
                db=db, project_id=str(project.id), template_id=str(payload.project_template_id)
            )

        # In-app notifications for internal project roles.
        # Project has already been committed above, so failures here won't roll back creation.
        try:
            _notify_project_roles_created_in_app(db, project)
        except Exception:
            db.rollback()
            logger.exception("project_created_in_app_notifications_failed project_id=%s", project.id)

        return project

    @staticmethod
    def get(db: Session, project_id: str):
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    @staticmethod
    def get_by_number(db: Session, number: str):
        if not number:
            raise HTTPException(status_code=404, detail="Project not found")
        project = db.query(Project).filter(Project.number == number).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    @staticmethod
    def list(
        db: Session,
        subscriber_id: str | None,
        status: str | None,
        project_type: str | None,
        priority: str | None,
        owner_person_id: str | None,
        manager_person_id: str | None,
        project_manager_person_id: str | None,
        assistant_manager_person_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
        search: str | None = None,
        filters_payload: list[Any] | None = None,
    ):
        query = db.query(Project)
        if subscriber_id:
            query = query.filter(Project.subscriber_id == coerce_uuid(subscriber_id))
        if status:
            query = query.filter(Project.status == validate_enum(status, ProjectStatus, "status"))
        if project_type:
            query = query.filter(Project.project_type == validate_enum(project_type, ProjectType, "project_type"))
        if priority:
            query = query.filter(Project.priority == validate_enum(priority, ProjectPriority, "priority"))
        if owner_person_id:
            query = query.filter(Project.owner_person_id == owner_person_id)
        if manager_person_id:
            query = query.filter(Project.manager_person_id == manager_person_id)
        if project_manager_person_id:
            query = query.filter(Project.project_manager_person_id == coerce_uuid(project_manager_person_id))
        if assistant_manager_person_id:
            query = query.filter(Project.assistant_manager_person_id == coerce_uuid(assistant_manager_person_id))
        if search and search.strip():
            like_term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    Project.name.ilike(like_term),
                    Project.code.ilike(like_term),
                    Project.number.ilike(like_term),
                    Project.customer_address.ilike(like_term),
                    Project.region.ilike(like_term),
                )
            )
        if is_active is None:
            query = query.filter(Project.is_active.is_(True))
        else:
            query = query.filter(Project.is_active == is_active)
        if filters_payload:
            from app.services.filter_engine import apply_filter_payload

            query = apply_filter_payload(query, "Project", filters_payload)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Project.created_at, "name": Project.name, "priority": Project.priority},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def chart_summary(db: Session) -> dict:
        """Get status count aggregation for chart display."""
        rows = (
            db.query(Project.status, func.count(Project.id))
            .filter(Project.is_active.is_(True))
            .group_by(Project.status)
            .all()
        )
        counts = {status.value: count for status, count in rows if status}
        data = [{"status": status.value, "count": counts.get(status.value, 0)} for status in ProjectStatus]
        return {"series": [{"label": "Projects", "data": data}]}

    @staticmethod
    def kanban_view(db: Session) -> dict:
        """Get kanban board columns and project records."""
        columns = [{"id": status.value, "title": status.value.replace("_", " ").title()} for status in ProjectStatus]
        projects_list = db.query(Project).filter(Project.is_active.is_(True)).order_by(Project.updated_at.desc()).all()
        records = []
        for project in projects_list:
            records.append(
                {
                    "id": str(project.id),
                    "name": project.name,
                    "project_type": project.project_type.value if project.project_type else None,
                    "status": project.status.value if project.status else None,
                    "due_date": project.due_at.date().isoformat() if project.due_at else None,
                }
            )
        return {"columns": columns, "records": records}

    @staticmethod
    def gantt_view(db: Session) -> dict:
        """Get gantt chart items with dates."""
        projects_list = db.query(Project).filter(Project.is_active.is_(True)).order_by(Project.updated_at.desc()).all()
        items = []
        for project in projects_list:
            start_dt = project.start_at or project.created_at
            due_dt = project.due_at or start_dt
            items.append(
                {
                    "id": str(project.id),
                    "name": project.name,
                    "start_date": start_dt.date().isoformat() if start_dt else None,
                    "due_date": due_dt.date().isoformat() if due_dt else None,
                }
            )
        return {"items": items}

    @staticmethod
    def update_gantt_date(db: Session, project_id: str, field: str, value: str) -> dict:
        """Update a date field on a project from gantt chart drag."""
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        field_map = {
            "due_date": "due_at",
            "start_date": "start_at",
            "completed_date": "completed_at",
            "due_at": "due_at",
            "start_at": "start_at",
            "completed_at": "completed_at",
        }
        if field not in field_map:
            raise HTTPException(status_code=400, detail="Invalid field")
        try:
            target_day = date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid date") from exc
        setattr(
            project,
            field_map[field],
            datetime.combine(target_day, time(23, 59, 59), tzinfo=UTC),
        )
        db.commit()
        return {"status": "ok", "field": field, "value": target_day.isoformat()}

    @staticmethod
    def update_status(db: Session, project_id: str, new_status: str) -> dict:
        """Move a project to a new status (kanban card move)."""
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            project.status = ProjectStatus(new_status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid status") from exc
        if project.status == ProjectStatus.completed and project.completed_at is None:
            project.completed_at = datetime.now(UTC)
        db.commit()
        return {"status": "ok"}

    @staticmethod
    def delete(db: Session, project_id: str):
        """Soft delete a project."""
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        project.is_active = False
        db.commit()

    @staticmethod
    def update(db: Session, project_id: str, payload: ProjectUpdate):
        project = db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        previous_status = project.status
        previous_template_id = str(project.project_template_id) if project.project_template_id else None
        data = payload.model_dump(exclude_unset=True)
        if data.get("created_by_person_id"):
            _ensure_person(db, str(data["created_by_person_id"]))
        if data.get("owner_person_id"):
            _ensure_person(db, str(data["owner_person_id"]))
        if data.get("manager_person_id"):
            _ensure_person(db, str(data["manager_person_id"]))
        if data.get("project_template_id"):
            _ensure_project_template(db, str(data["project_template_id"]))
        if data.get("lead_id"):
            _ensure_lead(db, str(data["lead_id"]))
        # Auto-assign PM based on region if region changes and no PM is set
        new_region = data.get("region")
        current_pm = data.get("manager_person_id") if "manager_person_id" in data else project.manager_person_id
        if new_region:
            auto_pm, auto_spc = Projects._get_region_pm_assignments(db, new_region)
            if auto_pm and not current_pm:
                data["manager_person_id"] = coerce_uuid(auto_pm)
            if auto_pm and not project.project_manager_person_id and "project_manager_person_id" not in data:
                data["project_manager_person_id"] = coerce_uuid(auto_pm)
            if auto_spc and not project.assistant_manager_person_id and "assistant_manager_person_id" not in data:
                data["assistant_manager_person_id"] = coerce_uuid(auto_spc)
        for key, value in data.items():
            setattr(project, key, value)
        if data.get("status") == ProjectStatus.completed and project.completed_at is None:
            project.completed_at = datetime.now(UTC)
        db.commit()
        db.refresh(project)

        # Emit events based on status changes
        new_status = project.status
        if new_status == ProjectStatus.completed and previous_status != ProjectStatus.completed:
            customer_name = None
            if project.subscriber and project.subscriber.person:
                person = project.subscriber.person
                customer_name = person.display_name or person.email
            if not customer_name and project.lead_id:
                lead = db.get(Lead, project.lead_id)
                if lead and lead.person:
                    customer_name = lead.person.display_name or lead.person.email
            emit_event(
                db,
                EventType.project_completed,
                {
                    "project_id": str(project.id),
                    "name": project.name,
                    "from_status": previous_status.value if previous_status else None,
                    "to_status": new_status.value,
                    "customer_name": customer_name,
                },
                project_id=project.id,
                subscriber_id=project.subscriber_id,
            )
            _notify_customer_project_completed(db, project)
        elif new_status == ProjectStatus.canceled and previous_status != ProjectStatus.canceled:
            emit_event(
                db,
                EventType.project_canceled,
                {
                    "project_id": str(project.id),
                    "name": project.name,
                    "from_status": previous_status.value if previous_status else None,
                    "to_status": new_status.value,
                },
                project_id=project.id,
                subscriber_id=project.subscriber_id,
            )
        elif previous_status != new_status or len(data) > 1:
            # Emit generic update if status changed or other fields updated
            emit_event(
                db,
                EventType.project_updated,
                {
                    "project_id": str(project.id),
                    "name": project.name,
                    "status": new_status.value if new_status else None,
                    "changed_fields": list(data.keys()),
                },
                project_id=project.id,
                subscriber_id=project.subscriber_id,
            )

        if "project_template_id" in data:
            new_template_id = str(project.project_template_id) if project.project_template_id else None
            if previous_template_id != new_template_id:
                ProjectTemplateTasks.replace_project_tasks(
                    db=db, project_id=str(project.id), template_id=new_template_id
                )
        # Persist notifications/events queued after the initial project update commit.
        db.commit()
        return project


class ProjectTemplates(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectTemplateCreate):
        data = payload.model_dump()
        template = ProjectTemplate(**data)
        db.add(template)
        db.commit()
        db.refresh(template)
        return template

    @staticmethod
    def get(db: Session, template_id: str):
        template = db.get(ProjectTemplate, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Project template not found")
        return template

    @staticmethod
    def list(
        db: Session,
        project_type: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProjectTemplate)
        if project_type:
            query = query.filter(
                ProjectTemplate.project_type == validate_enum(project_type, ProjectType, "project_type")
            )
        if is_active is None:
            query = query.filter(ProjectTemplate.is_active.is_(True))
        else:
            query = query.filter(ProjectTemplate.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": ProjectTemplate.created_at,
                "name": ProjectTemplate.name,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, template_id: str, payload: ProjectTemplateUpdate):
        template = db.get(ProjectTemplate, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Project template not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(template, key, value)
        db.commit()
        db.refresh(template)
        return template

    @staticmethod
    def delete(db: Session, template_id: str):
        template = db.get(ProjectTemplate, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Project template not found")
        template.is_active = False
        db.commit()

    @staticmethod
    def list_tasks(db: Session, template_id: str):
        return (
            db.query(ProjectTemplateTask)
            .filter(ProjectTemplateTask.template_id == template_id)
            .filter(ProjectTemplateTask.is_active.is_(True))
            .order_by(ProjectTemplateTask.sort_order.asc(), ProjectTemplateTask.created_at.asc())
            .all()
        )


class ProjectTemplateTasks(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectTemplateTaskCreate):
        _ensure_project_template(db, str(payload.template_id))
        data = payload.model_dump()
        task = ProjectTemplateTask(**data)
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def get(db: Session, task_id: str):
        task = db.get(ProjectTemplateTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project template task not found")
        return task

    @staticmethod
    def list(
        db: Session,
        template_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProjectTemplateTask)
        if template_id:
            query = query.filter(ProjectTemplateTask.template_id == template_id)
        if is_active is None:
            query = query.filter(ProjectTemplateTask.is_active.is_(True))
        else:
            query = query.filter(ProjectTemplateTask.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": ProjectTemplateTask.created_at,
                "sort_order": ProjectTemplateTask.sort_order,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, task_id: str, payload: ProjectTemplateTaskUpdate):
        task = db.get(ProjectTemplateTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project template task not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(task, key, value)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def delete(db: Session, task_id: str):
        task = db.get(ProjectTemplateTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project template task not found")
        task.is_active = False
        db.query(ProjectTemplateTaskDependency).filter(
            ProjectTemplateTaskDependency.template_task_id == task.id
        ).delete(synchronize_session=False)
        db.query(ProjectTemplateTaskDependency).filter(
            ProjectTemplateTaskDependency.depends_on_template_task_id == task.id
        ).delete(synchronize_session=False)
        db.commit()

    @staticmethod
    def replace_project_tasks(db: Session, project_id: str, template_id: str | None):
        project_uuid = coerce_uuid(project_id)
        template_task_ids_subquery = select(ProjectTask.id).where(
            ProjectTask.project_id == project_uuid,
            ProjectTask.template_task_id.isnot(None),
        )
        db.query(ProjectTaskDependency).filter(ProjectTaskDependency.task_id.in_(template_task_ids_subquery)).delete(
            synchronize_session=False
        )
        db.query(ProjectTaskDependency).filter(
            ProjectTaskDependency.depends_on_task_id.in_(template_task_ids_subquery)
        ).delete(synchronize_session=False)
        db.query(ProjectTask).filter(
            ProjectTask.project_id == project_uuid,
            ProjectTask.template_task_id.isnot(None),
        ).delete(synchronize_session=False)
        if not template_id:
            db.commit()
            return
        template_tasks = (
            db.query(ProjectTemplateTask)
            .filter(ProjectTemplateTask.template_id == template_id)
            .filter(ProjectTemplateTask.is_active.is_(True))
            .order_by(ProjectTemplateTask.sort_order.asc(), ProjectTemplateTask.created_at.asc())
            .all()
        )
        task_id_map: dict[str, str] = {}
        task_obj_map: dict[str, ProjectTask] = {}
        for template_task in template_tasks:
            data: dict = {
                "project_id": project_uuid,
                "title": template_task.title,
                "template_task_id": template_task.id,
            }
            number = generate_number(
                db=db,
                domain=SettingDomain.numbering,
                sequence_key="project_task_number",
                enabled_key="project_task_number_enabled",
                prefix_key="project_task_number_prefix",
                padding_key="project_task_number_padding",
                start_key="project_task_number_start",
            )
            if number:
                data["number"] = number
            if template_task.description:
                data["description"] = template_task.description
            if template_task.status:
                data["status"] = template_task.status
            if template_task.priority:
                data["priority"] = template_task.priority
            if template_task.effort_hours is not None:
                data["effort_hours"] = template_task.effort_hours
            task = ProjectTask(**data)
            db.add(task)
            db.flush()
            task_id_map[str(template_task.id)] = str(task.id)
            task_obj_map[str(task.id)] = task

        template_task_ids = [template_task.id for template_task in template_tasks]
        dep_graph: dict[str, list[str]] = {}
        if template_task_ids:
            dependencies = (
                db.query(ProjectTemplateTaskDependency)
                .filter(ProjectTemplateTaskDependency.template_task_id.in_(template_task_ids))
                .all()
            )
            for dependency in dependencies:
                task_id = task_id_map.get(str(dependency.template_task_id))
                depends_on_id = task_id_map.get(str(dependency.depends_on_template_task_id))
                if not task_id or not depends_on_id or task_id == depends_on_id:
                    continue
                dep_graph.setdefault(task_id, []).append(depends_on_id)
                db.add(
                    ProjectTaskDependency(
                        task_id=task_id,
                        depends_on_task_id=depends_on_id,
                        dependency_type=dependency.dependency_type,
                        lag_days=dependency.lag_days,
                    )
                )

        # Auto-calculate start_at/due_at from effort_hours and dependencies
        project = db.get(Project, project_uuid)
        project_start = project.start_at if project and project.start_at else datetime.now(UTC)
        _calculate_task_dates(task_obj_map, dep_graph, project_start)

        db.commit()


def _calculate_task_dates(
    task_obj_map: dict[str, ProjectTask],
    dep_graph: dict[str, list[str]],
    project_start: datetime,
) -> None:
    """Calculate start_at/due_at for tasks based on effort_hours and dependencies.

    Tasks with no predecessors start at project_start.
    Tasks with predecessors start at the latest predecessor due_at.
    due_at = start_at + effort_hours (if effort_hours is set).
    """
    resolved: dict[str, datetime] = {}

    def _resolve_due(task_id: str) -> datetime | None:
        if task_id in resolved:
            return resolved[task_id]
        task = task_obj_map.get(task_id)
        if not task:
            return None

        predecessors = dep_graph.get(task_id, [])
        if predecessors:
            pred_dues = [_resolve_due(pid) for pid in predecessors]
            valid_dues = [d for d in pred_dues if d is not None]
            start = max(valid_dues) if valid_dues else project_start
        else:
            start = project_start

        task.start_at = start
        if task.effort_hours:
            task.due_at = start + timedelta(hours=task.effort_hours)
            resolved[task_id] = task.due_at
        else:
            resolved[task_id] = start
        return resolved[task_id]

    for task_id in task_obj_map:
        _resolve_due(task_id)


class ProjectTasks(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectTaskCreate):
        project = db.get(Project, payload.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if payload.parent_task_id:
            parent = db.get(ProjectTask, payload.parent_task_id)
            if not parent:
                raise HTTPException(status_code=404, detail="Parent task not found")
        if payload.assigned_to_person_id:
            _ensure_person(db, str(payload.assigned_to_person_id))
        if payload.created_by_person_id:
            _ensure_person(db, str(payload.created_by_person_id))
        if payload.ticket_id:
            ticket = db.get(Ticket, payload.ticket_id)
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found")
        if payload.work_order_id:
            work_order = db.get(WorkOrder, payload.work_order_id)
            if not work_order:
                raise HTTPException(status_code=404, detail="Work order not found")
        data = payload.model_dump(exclude={"assigned_to_person_ids"})
        fields_set = payload.model_fields_set
        assignee_ids: list[str] | None = None
        if "assigned_to_person_ids" in fields_set:
            assignee_ids = [str(value) for value in (payload.assigned_to_person_ids or [])]
        elif payload.assigned_to_person_id:
            assignee_ids = [str(payload.assigned_to_person_id)]
        number = generate_number(
            db=db,
            domain=SettingDomain.numbering,
            sequence_key="project_task_number",
            enabled_key="project_task_number_enabled",
            prefix_key="project_task_number_prefix",
            padding_key="project_task_number_padding",
            start_key="project_task_number_start",
        )
        if number:
            data["number"] = number
        if "status" not in fields_set:
            default_status = settings_spec.resolve_value(db, SettingDomain.projects, "default_task_status")
            if default_status:
                data["status"] = validate_enum(default_status, TaskStatus, "status")
        if "priority" not in fields_set:
            default_priority = settings_spec.resolve_value(db, SettingDomain.projects, "default_task_priority")
            if default_priority:
                data["priority"] = validate_enum(default_priority, TaskPriority, "priority")
        task = ProjectTask(**data)
        db.add(task)
        db.flush()
        _apply_fiber_stage_defaults(db, task)
        if task.status == TaskStatus.done and not task.completed_at:
            task.completed_at = datetime.now(UTC)
        _sync_task_sla_clock(db, task)
        _sync_project_task_assignees(db, task, assignee_ids)
        db.commit()
        db.refresh(task)
        if task.assigned_to_person_id:
            assigned_to = db.get(Person, task.assigned_to_person_id)
            if assigned_to:
                created_by = None
                if task.created_by_person_id:
                    created_by = db.get(Person, task.created_by_person_id)
                _notify_project_task_assigned(
                    db=db,
                    task=task,
                    project=project,
                    assigned_to=assigned_to,
                    created_by=created_by,
                )
        return task

    @staticmethod
    def get(db: Session, task_id: str):
        task = db.get(ProjectTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project task not found")
        return task

    @staticmethod
    def get_by_number(db: Session, number: str):
        if not number:
            raise HTTPException(status_code=404, detail="Project task not found")
        task = db.query(ProjectTask).filter(ProjectTask.number == number).first()
        if not task:
            raise HTTPException(status_code=404, detail="Project task not found")
        return task

    @staticmethod
    def list(
        db: Session,
        project_id: str | None,
        status: str | None,
        priority: str | None,
        assigned_to_person_id: str | None,
        parent_task_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
        include_assigned: bool = False,
        filters_payload: list[Any] | None = None,
    ):
        query = db.query(ProjectTask)
        if include_assigned:
            query = query.options(
                selectinload(ProjectTask.assigned_to),
                selectinload(ProjectTask.assignees).selectinload(ProjectTaskAssignee.person),
            )
        if project_id:
            query = query.filter(ProjectTask.project_id == project_id)
        if status:
            query = query.filter(ProjectTask.status == validate_enum(status, TaskStatus, "status"))
        if priority:
            query = query.filter(ProjectTask.priority == validate_enum(priority, TaskPriority, "priority"))
        if assigned_to_person_id:
            assigned_uuid = coerce_uuid(assigned_to_person_id)
            query = query.filter(
                or_(
                    ProjectTask.assigned_to_person_id == assigned_uuid,
                    exists().where(
                        ProjectTaskAssignee.task_id == ProjectTask.id,
                        ProjectTaskAssignee.person_id == assigned_uuid,
                    ),
                )
            )
        if parent_task_id:
            query = query.filter(ProjectTask.parent_task_id == parent_task_id)
        if is_active is None:
            query = query.filter(ProjectTask.is_active.is_(True))
        else:
            query = query.filter(ProjectTask.is_active == is_active)
        if filters_payload:
            from app.services.filter_engine import apply_filter_payload

            query = apply_filter_payload(query, "Project Task", filters_payload)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": ProjectTask.created_at,
                "status": ProjectTask.status,
                "priority": ProjectTask.priority,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, task_id: str, payload: ProjectTaskUpdate):
        task = db.get(ProjectTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project task not found")
        previous_status = task.status
        changed_fields: list[str] = []
        data = payload.model_dump(exclude_unset=True)
        assignee_ids: list[str] | None = None
        if "assigned_to_person_ids" in payload.model_fields_set:
            assignee_ids = [str(value) for value in (payload.assigned_to_person_ids or [])]
        elif "assigned_to_person_id" in data:
            if data.get("assigned_to_person_id"):
                assignee_ids = [str(data["assigned_to_person_id"])]
            else:
                assignee_ids = []
        data.pop("assigned_to_person_ids", None)
        if "project_id" in data:
            project = db.get(Project, data["project_id"])
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
        if data.get("parent_task_id"):
            parent = db.get(ProjectTask, data["parent_task_id"])
            if not parent:
                raise HTTPException(status_code=404, detail="Parent task not found")
        if data.get("assigned_to_person_id"):
            _ensure_person(db, str(data["assigned_to_person_id"]))
        if data.get("created_by_person_id"):
            _ensure_person(db, str(data["created_by_person_id"]))
        if data.get("ticket_id"):
            ticket = db.get(Ticket, data["ticket_id"])
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found")
        if data.get("work_order_id"):
            work_order = db.get(WorkOrder, data["work_order_id"])
            if not work_order:
                raise HTTPException(status_code=404, detail="Work order not found")
        changed_fields.extend(list(data.keys()))
        for key, value in data.items():
            setattr(task, key, value)
        _apply_fiber_stage_defaults(db, task)
        if task.status == TaskStatus.done and not task.completed_at:
            task.completed_at = datetime.now(UTC)
        _sync_task_sla_clock(db, task)
        _sync_project_task_assignees(db, task, assignee_ids)
        db.commit()
        db.refresh(task)
        if (
            "assigned_to_person_ids" in payload.model_fields_set or "assigned_to_person_id" in payload.model_fields_set
        ) and ("assigned_to_person_ids" not in changed_fields):
            changed_fields.append("assigned_to_person_ids")

        event_payload: dict[str, object | None] = {
            "task_id": str(task.id),
            "project_id": str(task.project_id) if task.project_id else None,
            "title": task.title,
            "from_status": previous_status.value if previous_status else None,
            "to_status": task.status.value if task.status else None,
            "status": task.status.value if task.status else None,
            "priority": task.priority.value if task.priority else None,
            "changed_fields": changed_fields,
        }

        if previous_status != TaskStatus.done and task.status == TaskStatus.done:
            project = db.get(Project, task.project_id)
            if project:
                _notify_customer_task_completed(db, project, task)
                db.commit()
            emit_event(
                db,
                EventType.project_task_completed,
                event_payload,
                project_id=task.project_id,
            )
        elif previous_status != task.status or bool(changed_fields):
            emit_event(
                db,
                EventType.project_task_updated,
                event_payload,
                project_id=task.project_id,
            )
        return task

    @staticmethod
    def delete(db: Session, task_id: str):
        task = db.get(ProjectTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project task not found")
        task.is_active = False
        db.commit()


class ProjectTaskComments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectTaskCommentCreate):
        task = db.get(ProjectTask, payload.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Project task not found")
        if payload.author_person_id:
            _ensure_person(db, str(payload.author_person_id))
        comment = ProjectTaskComment(**payload.model_dump())
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def list(
        db: Session,
        task_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProjectTaskComment).options(selectinload(ProjectTaskComment.author))
        if task_id:
            query = query.filter(ProjectTaskComment.task_id == task_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ProjectTaskComment.created_at},
        )
        return apply_pagination(query, limit, offset).all()


class ProjectComments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ProjectCommentCreate):
        project = db.get(Project, payload.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if payload.author_person_id:
            _ensure_person(db, str(payload.author_person_id))
        comment = ProjectComment(**payload.model_dump())
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def update(db: Session, comment_id: str, payload: ProjectCommentUpdate):
        comment = db.get(ProjectComment, comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        data = payload.model_dump(exclude_unset=True)
        if "body" in data and data["body"] is None:
            data.pop("body")
        if not data:
            return comment
        for key, value in data.items():
            setattr(comment, key, value)
        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def list(
        db: Session,
        project_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ProjectComment).options(selectinload(ProjectComment.author))
        if project_id:
            query = query.filter(ProjectComment.project_id == project_id)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ProjectComment.created_at},
        )
        return apply_pagination(query, limit, offset).all()


projects = Projects()
project_tasks = ProjectTasks()
project_templates = ProjectTemplates()
project_template_tasks = ProjectTemplateTasks()
project_task_comments = ProjectTaskComments()
project_comments = ProjectComments()
