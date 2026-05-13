from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.workqueue import aggregator as agg_module
from app.services.workqueue.aggregator import build_workqueue
from app.services.workqueue.types import (
    ActionKind,
    ItemKind,
    WorkqueueAudience,
    WorkqueueItem,
)


def _item(kind, score, ts=None):
    return WorkqueueItem(
        kind=kind,
        item_id=uuid4(),
        title="x",
        subtitle=None,
        score=score,
        reason="r",
        urgency="high" if score >= 70 else "normal",
        deep_link="/",
        assignee_id=None,
        is_unassigned=True,
        happened_at=ts or datetime.now(UTC),
        actions=frozenset({ActionKind.open}),
        metadata={},
    )


class FakeProvider:
    def __init__(self, kind, items):
        self.kind = kind
        self._items = items

    def fetch(self, db, *, user, audience, scope, snoozed_ids, limit=50):
        return list(self._items)


def test_aggregator_uses_registered_providers(db_session, monkeypatch):
    user = SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"}, roles=set())
    fake_convs = FakeProvider(ItemKind.conversation, [_item(ItemKind.conversation, 100)])
    fake_tickets = FakeProvider(ItemKind.ticket, [_item(ItemKind.ticket, 80)])
    monkeypatch.setattr(agg_module, "PROVIDERS", (fake_convs, fake_tickets))

    view = build_workqueue(db_session, user)
    assert view.audience is WorkqueueAudience.self_
    assert len(view.right_now) == 2
    assert view.right_now[0].score == 100
    assert {s.kind for s in view.sections} == {
        ItemKind.conversation,
        ItemKind.ticket,
        ItemKind.lead,
        ItemKind.quote,
        ItemKind.task,
    }


def test_hero_band_capped(db_session, monkeypatch):
    user = SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"}, roles=set())
    items = [_item(ItemKind.ticket, 100 - i) for i in range(20)]
    monkeypatch.setattr(agg_module, "PROVIDERS", (FakeProvider(ItemKind.ticket, items),))
    view = build_workqueue(db_session, user)
    assert len(view.right_now) <= 6


def test_tie_break_by_kind_order(db_session, monkeypatch):
    user = SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"}, roles=set())
    same_score = 80
    same_ts = datetime.now(UTC)
    monkeypatch.setattr(
        agg_module,
        "PROVIDERS",
        (
            FakeProvider(ItemKind.task, [_item(ItemKind.task, same_score, same_ts)]),
            FakeProvider(ItemKind.conversation, [_item(ItemKind.conversation, same_score, same_ts)]),
        ),
    )
    view = build_workqueue(db_session, user)
    assert view.right_now[0].kind is ItemKind.conversation
    assert view.right_now[1].kind is ItemKind.task
