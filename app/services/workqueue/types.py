"""In-memory types for the Workqueue feature."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID


class ItemKind(str, enum.Enum):
    conversation = "conversation"
    ticket = "ticket"
    lead = "lead"
    quote = "quote"
    task = "task"


class ActionKind(str, enum.Enum):
    open = "open"
    snooze = "snooze"
    claim = "claim"
    complete = "complete"


class WorkqueueAudience(str, enum.Enum):
    self_ = "self"
    team = "team"
    org = "org"


Urgency = Literal["critical", "high", "normal", "low"]


def urgency_for_score(score: int) -> Urgency:
    if score >= 90:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 40:
        return "normal"
    return "low"


@dataclass(frozen=True)
class WorkqueueItem:
    kind: ItemKind
    item_id: UUID
    title: str
    subtitle: str | None
    score: int
    reason: str
    urgency: Urgency
    deep_link: str
    assignee_id: UUID | None
    is_unassigned: bool
    happened_at: datetime
    actions: frozenset[ActionKind]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkqueueSection:
    kind: ItemKind
    items: tuple[WorkqueueItem, ...]
    total: int


@dataclass(frozen=True)
class WorkqueueView:
    audience: WorkqueueAudience
    right_now: tuple[WorkqueueItem, ...]
    sections: tuple[WorkqueueSection, ...]
