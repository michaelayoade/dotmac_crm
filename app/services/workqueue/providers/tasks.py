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

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.projects import ProjectTask, ProjectTaskAssignee, TaskStatus
from app.services.workqueue.providers import register
from app.services.workqueue.scoring_config import PROVIDER_LIMIT, TASK_SCORES
from app.services.workqueue.types import (
    ActionKind,
    ItemKind,
    WorkqueueAudience,
    WorkqueueItem,
    urgency_for_score,
)

_OPEN = (TaskStatus.todo, TaskStatus.in_progress, TaskStatus.blocked)


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


class TasksProvider:
    """Workqueue provider that surfaces actionable project tasks."""

    kind = ItemKind.task

    def fetch(
        self,
        db: Session,
        *,
        user,
        audience: WorkqueueAudience,
        snoozed_ids: set[UUID],
        limit: int = PROVIDER_LIMIT,
    ) -> list[WorkqueueItem]:
        now = datetime.now(UTC)

        stmt = (
            select(ProjectTask)
            .options(selectinload(ProjectTask.assignees))
            .where(ProjectTask.is_active.is_(True))
            .where(ProjectTask.status.in_(_OPEN))
        )

        if audience is WorkqueueAudience.self_:
            stmt = stmt.join(
                ProjectTaskAssignee,
                ProjectTaskAssignee.task_id == ProjectTask.id,
            ).where(ProjectTaskAssignee.person_id == user.person_id)
        # WorkqueueAudience.team / org: surface every actionable task; for
        # ``team`` we additionally exclude tasks owned by other users in
        # Python (matching the conversations/tickets precedent which keeps
        # team scoping for a follow-up slice).

        if snoozed_ids:
            stmt = stmt.where(~ProjectTask.id.in_(snoozed_ids))

        rows = db.execute(stmt.limit(limit * 2)).scalars().unique().all()

        items: list[WorkqueueItem] = []
        for t in rows:
            assignee = _resolve_assignee(t)
            if audience is WorkqueueAudience.team and assignee not in (
                user.person_id,
                None,
            ):
                continue

            verdict = _classify(t, now)
            if verdict is None:
                continue
            reason, score = verdict
            actions = {ActionKind.open, ActionKind.snooze, ActionKind.complete}
            if assignee is None:
                actions.add(ActionKind.claim)
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
                    metadata={},
                )
            )
        items.sort(key=lambda i: -i.score)
        return items[:limit]


tasks_provider = register(TasksProvider())
