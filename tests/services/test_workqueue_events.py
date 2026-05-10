from uuid import uuid4

import pytest

from app.services.workqueue import events
from app.services.workqueue.types import ItemKind


def test_user_channel_name():
    user_id = uuid4()
    assert events.user_channel(user_id) == f"workqueue:user:{user_id}"


def test_team_channel_name():
    team_id = uuid4()
    assert events.team_channel(team_id) == f"workqueue:audience:team:{team_id}"


def test_org_channel_name():
    assert events.org_channel() == "workqueue:audience:org"


def test_emit_change_does_not_raise_on_redis_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(events, "_publish", fail)
    events.emit_change(
        kind=ItemKind.ticket,
        item_id=uuid4(),
        change="updated",
        affected_user_ids=[uuid4()],
    )


def test_emit_change_publishes_to_each_user_channel(monkeypatch):
    sent = []
    monkeypatch.setattr(events, "_publish", lambda chan, payload: sent.append((chan, payload)))
    user_a, user_b = uuid4(), uuid4()
    item_id = uuid4()
    events.emit_change(
        kind=ItemKind.task,
        item_id=item_id,
        change="added",
        affected_user_ids=[user_a, user_b],
    )
    channels = {c for c, _ in sent}
    assert events.user_channel(user_a) in channels
    assert events.user_channel(user_b) in channels
    assert all(p["type"] == "workqueue.changed" for _, p in sent)
    assert all(p["item_id"] == str(item_id) for _, p in sent)
