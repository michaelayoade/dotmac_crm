from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.projects import Project, ProjectComment, ProjectStatus, ProjectTask, ProjectTaskComment, TaskStatus
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def gather_project_context(db: Session, params: dict[str, Any]) -> str:
    project_id = params.get("project_id")
    if not project_id:
        raise ValueError("project_id is required")

    project = db.get(Project, coerce_uuid(project_id))
    if not project:
        raise ValueError("Project not found")

    max_tasks = min(int(params.get("max_tasks", 12)), 40)
    max_comments = min(int(params.get("max_comments", 6)), 20)
    max_chars = int(params.get("max_chars", 600))

    def _person_name(person_id) -> str | None:
        if not person_id:
            return None
        p = db.get(Person, person_id)
        if not p:
            return None
        return redact_text(p.display_name or "", max_chars=120) or None

    lines: list[str] = [
        f"Project ID: {str(project.id)[:8]}",
        f"Name: {redact_text(project.name or '', max_chars=200)}",
        f"Status: {project.status.value if isinstance(project.status, ProjectStatus) else str(project.status)}",
        f"Priority: {project.priority.value if hasattr(project.priority, 'value') else str(project.priority)}",
        f"Type: {project.project_type.value if project.project_type else 'unknown'}",
        f"Region: {redact_text(project.region or '', max_chars=80)}" if project.region else "Region: unknown",
        f"Start: {project.start_at.isoformat() if project.start_at else 'unknown'}",
        f"Due: {project.due_at.isoformat() if project.due_at else 'unknown'}",
        f"Updated: {project.updated_at.isoformat() if project.updated_at else 'unknown'}",
        f"Customer address: {redact_text(project.customer_address or '', max_chars=220)}",
        f"Description: {redact_text(project.description or '', max_chars=900)}",
    ]

    owner = _person_name(project.owner_person_id)
    if owner:
        lines.append(f"Owner: {owner}")
    manager = _person_name(project.project_manager_person_id) or _person_name(project.manager_person_id)
    if manager:
        lines.append(f"Manager: {manager}")

    # Tasks
    tasks = (
        db.query(ProjectTask)
        .filter(ProjectTask.project_id == project.id)
        .filter(ProjectTask.is_active.is_(True))
        .order_by(ProjectTask.updated_at.desc())
        .limit(max(1, max_tasks))
        .all()
    )
    if tasks:
        lines.append("Recent tasks (most recently updated):")
        for t in tasks:
            status = t.status.value if isinstance(t.status, TaskStatus) else str(t.status)
            assignee = _person_name(t.assigned_to_person_id) or "unassigned"
            title = redact_text(t.title or "", max_chars=220)
            desc = redact_text(t.description or "", max_chars=max_chars)
            due = t.due_at.isoformat() if t.due_at else "unknown"
            lines.append(f"  - {status} | {assignee} | due={due} | {title}: {desc}")

    # Comments (project-level + task-level)
    comments = (
        db.query(ProjectComment)
        .filter(ProjectComment.project_id == project.id)
        .order_by(ProjectComment.created_at.desc())
        .limit(max(1, max_comments))
        .all()
    )
    task_comments = (
        db.query(ProjectTaskComment)
        .join(ProjectTask, ProjectTask.id == ProjectTaskComment.task_id)
        .filter(ProjectTask.project_id == project.id)
        .order_by(ProjectTaskComment.created_at.desc())
        .limit(max(1, max_comments))
        .all()
    )

    combined = [("project", c.created_at, c.author_person_id, c.body) for c in comments] + [
        ("task", c.created_at, c.author_person_id, c.body) for c in task_comments
    ]
    combined.sort(key=lambda row: row[1], reverse=True)
    combined = combined[:max_comments]

    if combined:
        lines.append("Recent comments:")
        for kind, created_at, author_person_id, body in combined:
            who = _person_name(author_person_id) or "unknown"
            when = created_at.isoformat() if created_at else "unknown"
            lines.append(f"  - [{kind}] {when} {who}: {redact_text(body or '', max_chars=max_chars)}")

    # High-level status counts help the model reason about bottlenecks.
    if tasks:

        def _count(status: TaskStatus) -> int:
            return sum(1 for t in tasks if getattr(t.status, "value", t.status) == status.value)

        lines.append(
            "Task status counts (sample): "
            + ", ".join(
                [
                    f"todo={_count(TaskStatus.todo)}",
                    f"in_progress={_count(TaskStatus.in_progress)}",
                    f"blocked={_count(TaskStatus.blocked)}",
                    f"done={_count(TaskStatus.done)}",
                ]
            )
        )

    return "\n".join([line for line in lines if line.strip()])
