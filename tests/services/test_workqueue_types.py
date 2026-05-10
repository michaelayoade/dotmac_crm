from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.services.workqueue.types import (
    ActionKind,
    ItemKind,
    WorkqueueAudience,
    WorkqueueItem,
    WorkqueueView,
    urgency_for_score,
)


def test_item_kind_values():
    assert ItemKind.conversation.value == "conversation"
    assert ItemKind.ticket.value == "ticket"
    assert ItemKind.lead.value == "lead"
    assert ItemKind.quote.value == "quote"
    assert ItemKind.task.value == "task"


def test_action_kind_values():
    assert {a.value for a in ActionKind} == {"open", "snooze", "claim", "complete"}


def test_audience_values():
    assert {a.value for a in WorkqueueAudience} == {"self", "team", "org"}


@pytest.mark.parametrize(
    "score,expected",
    [(100, "critical"), (90, "critical"), (89, "high"), (70, "high"),
     (69, "normal"), (40, "normal"), (39, "low"), (0, "low")],
)
def test_urgency_bands(score, expected):
    assert urgency_for_score(score) == expected


def test_workqueue_item_is_frozen():
    item = WorkqueueItem(
        kind=ItemKind.ticket,
        item_id=uuid4(),
        title="T-1",
        subtitle=None,
        score=80,
        reason="overdue",
        urgency="high",
        deep_link="/admin/tickets/1",
        assignee_id=None,
        is_unassigned=True,
        happened_at=datetime.now(UTC),
        actions=frozenset({ActionKind.open, ActionKind.claim}),
        metadata={},
    )
    with pytest.raises((AttributeError, TypeError, Exception)):
        item.score = 50  # type: ignore[misc]


def test_workqueue_view_holds_band_and_sections():
    v = WorkqueueView(audience=WorkqueueAudience.self_, right_now=(), sections=())
    assert v.audience is WorkqueueAudience.self_
    assert v.right_now == ()
    assert v.sections == ()
