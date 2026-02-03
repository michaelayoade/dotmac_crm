"""Query builders for project-related models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.projects import (
    Project,
    ProjectComment,
    ProjectPriority,
    ProjectStatus,
    ProjectTask,
    ProjectTemplate,
    TaskPriority,
    TaskStatus,
)
from app.queries.base import BaseQuery
from app.services.common import coerce_uuid, validate_enum

if TYPE_CHECKING:
    from uuid import UUID


class ProjectQuery(BaseQuery[Project]):
    """Query builder for Project model.

    Usage:
        projects = (
            ProjectQuery(db)
            .by_subscriber(subscriber_id)
            .by_status(ProjectStatus.in_progress)
            .by_owner(owner_id)
            .active_only()
            .order_by("created_at", "desc")
            .paginate(50, 0)
            .all()
        )
    """

    model_class = Project
    ordering_fields = {
        "created_at": Project.created_at,
        "updated_at": Project.updated_at,
        "name": Project.name,
        "status": Project.status,
        "priority": Project.priority,
        "target_start_date": Project.start_at,
        "target_end_date": Project.due_at,
        "start_at": Project.start_at,
        "due_at": Project.due_at,
    }

    def by_subscriber(self, subscriber_id: "UUID | str | None") -> "ProjectQuery":
        """Filter by subscriber ID."""
        if not subscriber_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            Project.subscriber_id == coerce_uuid(subscriber_id)
        )
        return clone

    def by_status(self, status: ProjectStatus | str | None) -> "ProjectQuery":
        """Filter by project status."""
        if not status:
            return self
        clone = self._clone()
        if isinstance(status, str):
            status = validate_enum(status, ProjectStatus, "status")
        clone._query = clone._query.filter(Project.status == status)
        return clone

    def by_statuses(self, statuses: list[ProjectStatus | str]) -> "ProjectQuery":
        """Filter by multiple statuses."""
        if not statuses:
            return self
        clone = self._clone()
        status_enums = [
            validate_enum(s, ProjectStatus, "status") if isinstance(s, str) else s
            for s in statuses
        ]
        clone._query = clone._query.filter(Project.status.in_(status_enums))
        return clone

    def exclude_statuses(self, statuses: list[ProjectStatus | str]) -> "ProjectQuery":
        """Exclude projects with these statuses."""
        if not statuses:
            return self
        clone = self._clone()
        status_enums = [
            validate_enum(s, ProjectStatus, "status") if isinstance(s, str) else s
            for s in statuses
        ]
        clone._query = clone._query.filter(Project.status.notin_(status_enums))
        return clone

    def by_priority(self, priority: ProjectPriority | str | None) -> "ProjectQuery":
        """Filter by project priority."""
        if not priority:
            return self
        clone = self._clone()
        if isinstance(priority, str):
            priority = validate_enum(priority, ProjectPriority, "priority")
        clone._query = clone._query.filter(Project.priority == priority)
        return clone

    def by_owner(self, person_id: "UUID | str | None") -> "ProjectQuery":
        """Filter by owner person ID."""
        if not person_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            Project.owner_person_id == coerce_uuid(person_id)
        )
        return clone

    def by_manager(self, person_id: "UUID | str | None") -> "ProjectQuery":
        """Filter by manager person ID."""
        if not person_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            Project.manager_person_id == coerce_uuid(person_id)
        )
        return clone

    def by_template(self, template_id: "UUID | str | None") -> "ProjectQuery":
        """Filter by project template ID."""
        if not template_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            Project.project_template_id == coerce_uuid(template_id)
        )
        return clone

    def in_progress(self) -> "ProjectQuery":
        """Filter to in-progress projects."""
        return self.by_statuses([
            ProjectStatus.active,
            ProjectStatus.on_hold,
        ])

    def open_projects(self) -> "ProjectQuery":
        """Filter to open (non-completed, non-canceled) projects."""
        return self.exclude_statuses([
            ProjectStatus.completed,
            ProjectStatus.canceled,
        ])

    def for_site_surveys(self) -> "ProjectQuery":
        """Filter projects available for site surveys (not completed/canceled)."""
        return self.exclude_statuses([
            ProjectStatus.completed,
            ProjectStatus.canceled,
        ]).order_by("name", "asc")


class ProjectTaskQuery(BaseQuery[ProjectTask]):
    """Query builder for ProjectTask model."""

    model_class = ProjectTask
    ordering_fields = {
        "created_at": ProjectTask.created_at,
        "updated_at": ProjectTask.updated_at,
        "name": ProjectTask.title,
        "title": ProjectTask.title,
        "status": ProjectTask.status,
        "priority": ProjectTask.priority,
        "start_at": ProjectTask.start_at,
        "due_at": ProjectTask.due_at,
    }

    def by_project(self, project_id: "UUID | str | None") -> "ProjectTaskQuery":
        """Filter by project ID."""
        if not project_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            ProjectTask.project_id == coerce_uuid(project_id)
        )
        return clone

    def by_status(self, status: TaskStatus | str | None) -> "ProjectTaskQuery":
        """Filter by task status."""
        if not status:
            return self
        clone = self._clone()
        if isinstance(status, str):
            status = validate_enum(status, TaskStatus, "status")
        clone._query = clone._query.filter(ProjectTask.status == status)
        return clone

    def by_statuses(self, statuses: list[TaskStatus | str]) -> "ProjectTaskQuery":
        """Filter by multiple statuses."""
        if not statuses:
            return self
        clone = self._clone()
        status_enums = [
            validate_enum(s, TaskStatus, "status") if isinstance(s, str) else s
            for s in statuses
        ]
        clone._query = clone._query.filter(ProjectTask.status.in_(status_enums))
        return clone

    def by_priority(self, priority: TaskPriority | str | None) -> "ProjectTaskQuery":
        """Filter by task priority."""
        if not priority:
            return self
        clone = self._clone()
        if isinstance(priority, str):
            priority = validate_enum(priority, TaskPriority, "priority")
        clone._query = clone._query.filter(ProjectTask.priority == priority)
        return clone

    def by_assignee(self, person_id: "UUID | str | None") -> "ProjectTaskQuery":
        """Filter by assignee person ID."""
        if not person_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            ProjectTask.assigned_to_person_id == coerce_uuid(person_id)
        )
        return clone

    def unassigned(self) -> "ProjectTaskQuery":
        """Filter to only unassigned tasks."""
        clone = self._clone()
        clone._query = clone._query.filter(ProjectTask.assigned_to_person_id.is_(None))
        return clone

    def pending(self) -> "ProjectTaskQuery":
        """Filter to pending tasks (not started)."""
        return self.by_statuses([
            TaskStatus.todo,
            TaskStatus.blocked,
        ])

    def in_progress(self) -> "ProjectTaskQuery":
        """Filter to in-progress tasks."""
        return self.by_status(TaskStatus.in_progress)

    def completed(self) -> "ProjectTaskQuery":
        """Filter to completed tasks."""
        return self.by_statuses([
            TaskStatus.done,
            TaskStatus.canceled,
        ])


class ProjectTemplateQuery(BaseQuery[ProjectTemplate]):
    """Query builder for ProjectTemplate model."""

    model_class = ProjectTemplate
    ordering_fields = {
        "created_at": ProjectTemplate.created_at,
        "name": ProjectTemplate.name,
    }

    def search(self, term: str | None) -> "ProjectTemplateQuery":
        """Search templates by name."""
        if not term or not term.strip():
            return self
        clone = self._clone()
        like_term = f"%{term.strip()}%"
        clone._query = clone._query.filter(ProjectTemplate.name.ilike(like_term))
        return clone
