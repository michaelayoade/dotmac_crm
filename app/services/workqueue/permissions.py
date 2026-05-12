"""Audience resolution and per-action authorization for the Workqueue."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.services.workqueue.types import WorkqueueAudience


class _UserLike(Protocol):
    person_id: UUID
    permissions: set[str]


_NATURAL_BY_PRIORITY = (
    ("workqueue:audience:org", WorkqueueAudience.org),
    ("workqueue:audience:team", WorkqueueAudience.team),
)


def has_workqueue_view(user: _UserLike) -> bool:
    return "workqueue:view" in user.permissions


def _natural_audience(user: _UserLike) -> WorkqueueAudience:
    for perm, audience in _NATURAL_BY_PRIORITY:
        if perm in user.permissions:
            return audience
    return WorkqueueAudience.self_


def resolve_audience(user: _UserLike, requested: str | None = None) -> WorkqueueAudience:
    """Resolve the requested audience.

    The workqueue defaults to the logged-in user's own queue. Broader team/org
    views remain available only when explicitly requested and permitted.
    """
    natural = _natural_audience(user)
    if requested is None:
        return WorkqueueAudience.self_

    try:
        wanted = WorkqueueAudience(requested)
    except ValueError:
        return natural

    rank = {WorkqueueAudience.self_: 0, WorkqueueAudience.team: 1, WorkqueueAudience.org: 2}
    return wanted if rank[wanted] <= rank[natural] else natural


def can_act_on_item(
    user: _UserLike,
    *,
    item_assignee_id: UUID | None,
    audience: WorkqueueAudience,
) -> bool:
    """Whether the user may take an inline action on an item rendered in `audience`."""
    if audience is WorkqueueAudience.self_:
        return item_assignee_id is not None and item_assignee_id == user.person_id

    return True
