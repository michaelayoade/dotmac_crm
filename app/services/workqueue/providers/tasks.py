"""Project-task provider for the Workqueue.

Surfaces actionable rows from the ``project_tasks`` table.  Classification
uses real model columns (``status``, ``due_at``); no metadata stashing is
required.

Assignment is resolved through the many-to-many ``ProjectTaskAssignee``
join table (composite PK on ``task_id``/``person_id`` — no surrogate id
column).  For ``self_`` audience we filter via a join; for ``team`` we
post-filter in Python so unassigned tasks are also included.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.projects import ProjectTask, TaskStatus
from app.services.workqueue.providers import register
from app.services.workqueue.scope import WorkqueueScope, apply_task_scope
from app.services.workqueue.scoring_config import PROVIDER_LIMIT, TASK_SCORES
from app.services.workqueue.types import (
    ActionKind,
    ItemKind,
    WorkqueueAudience,
    WorkqueueItem,
    urgency_for_score,
)

_OPEN = (TaskStatus.todo, TaskStatus.in_progress, TaskStatus.blocked)
logger = logging.getLogger(__name__)


def _due_at(t: ProjectTask) -> datetime | None:
    due = t.due_at
    if due is None:
        return None
    return due if due.tzinfo else due.replace(tzinfo=UTC)


def _classify(t: ProjectTask, now: datetime) -> tuple[str, int] | None:
    due = _due_at(t)
    if due is not None:
        delta = (due - now).total_seconds()
        if delta < 0:
            return "overdue", TASK_SCORES["overdue"]
        if delta < 24 * 3600:
            return "due_today", TASK_SCORES["due_today"]
    return None


def _resolve_assignee(t: ProjectTask) -> UUID | None:
    """Return the first assignee person id, or fall back to ``assigned_to_person_id``."""
    assignees = list(getattr(t, "assignees", None) or ())
    if assignees:
        return assignees[0].person_id
    return getattr(t, "assigned_to_person_id", None)


def _visibility_source(t: ProjectTask, assignee: UUID | None, scope: WorkqueueScope) -> str:
    project = getattr(t, "project", None)
    service_team_id = getattr(project, "service_team_id", None)
    if assignee == scope.person_id:
        return "direct_assignment"
    if assignee is not None and assignee in scope.accessible_person_ids:
        return "team_profile_assignment"
    if service_team_id is not None and service_team_id in scope.accessible_service_team_ids:
        return "service_team_ownership"
    return "unknown"


class TasksProvider:
    """Workqueue provider that surfaces actionable project tasks."""

    kind = ItemKind.task

    def fetch(
        self,
        db: Session,
        *,
        user,
        audience: WorkqueueAudience,
        scope: WorkqueueScope,
        snoozed_ids: set[UUID],
        limit: int = PROVIDER_LIMIT,
    ) -> list[WorkqueueItem]:
        now = datetime.now(UTC)

        stmt = (
            select(ProjectTask)
            .options(selectinload(ProjectTask.assignees), selectinload(ProjectTask.project))
            .where(ProjectTask.is_active.is_(True))
            .where(ProjectTask.status.in_(_OPEN))
        )
        stmt = apply_task_scope(stmt, scope)

        if snoozed_ids:
            stmt = stmt.where(~ProjectTask.id.in_(snoozed_ids))

        rows = db.execute(stmt.limit(limit * 2)).scalars().unique().all()

        items: list[WorkqueueItem] = []
        for t in rows:
            assignee = _resolve_assignee(t)

            verdict = _classify(t, now)
            if verdict is None:
                continue
            reason, score = verdict
            actions = {ActionKind.open, ActionKind.snooze, ActionKind.complete}
            if assignee is None:
                actions.add(ActionKind.claim)
            visibility_source = _visibility_source(t, assignee, scope)
            logger.info(
                "workqueue_item_included kind=task user_id=%s item_id=%s visibility_source=%s assignee_source=%s team_source=%s",
                scope.person_id,
                t.id,
                visibility_source,
                assignee,
                getattr(getattr(t, "project", None), "service_team_id", None),
            )
            items.append(
                WorkqueueItem(
                    kind=ItemKind.task,
                    item_id=t.id,
                    title=t.title,
                    subtitle=reason.replace("_", " ").title(),
                    score=score,
                    reason=reason,
                    urgency=urgency_for_score(score),
                    deep_link=f"/admin/projects/{t.project_id}/tasks/{t.id}",
                    assignee_id=assignee,
                    is_unassigned=assignee is None,
                    happened_at=t.updated_at or now,
                    actions=frozenset(actions),
                    metadata={"visibility_source": visibility_source},
                )
            )
        items.sort(key=lambda i: -i.score)
        return items[:limit]


tasks_provider = register(TasksProvider())
