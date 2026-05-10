from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.workqueue.actions import workqueue_actions
from app.services.workqueue.types import ItemKind


@pytest.fixture
def user():
    return SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view", "workqueue:claim"})


def test_snooze_validates_and_persists(db_session, user):
    item_id = uuid4()
    workqueue_actions.snooze(
        db_session,
        user,
        ItemKind.task,
        item_id,
        until=datetime.now(UTC) + timedelta(hours=1),
    )
    assert workqueue_actions.is_snoozed(db_session, user.person_id, ItemKind.task, item_id) is True


def test_complete_disallowed_for_lead(db_session, user):
    with pytest.raises(ValueError):
        workqueue_actions.complete(db_session, user, ItemKind.lead, uuid4())


def test_complete_dispatches_to_ticket_manager(db_session, user, ticket_factory, monkeypatch):
    t = ticket_factory(assignee_person_id=user.person_id)
    called = {"resolve": None}
    from app.services import tickets as tickets_service

    def fake_resolve(db, ticket_id, *, actor_id=None, **kwargs):
        called["resolve"] = (str(ticket_id), str(actor_id))

    monkeypatch.setattr(tickets_service.tickets, "resolve", fake_resolve)
    workqueue_actions.complete(db_session, user, ItemKind.ticket, t.id)
    assert called["resolve"] == (str(t.id), str(user.person_id))


def test_claim_unassigned_ticket(db_session, user, ticket_factory, monkeypatch):
    t = ticket_factory(assignee_person_id=None)
    called = {"assignee": None}
    from app.services import tickets as tickets_service

    def fake_assign(db, ticket_id, person_id, *, actor_id=None, **kwargs):
        called["assignee"] = (str(ticket_id), str(person_id))

    monkeypatch.setattr(tickets_service.tickets, "assign", fake_assign)
    workqueue_actions.claim(db_session, user, ItemKind.ticket, t.id)
    assert called["assignee"] == (str(t.id), str(user.person_id))


def test_claim_requires_permission(db_session, ticket_factory):
    no_claim_user = SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"})
    t = ticket_factory(assignee_person_id=None)
    with pytest.raises(PermissionError):
        workqueue_actions.claim(db_session, no_claim_user, ItemKind.ticket, t.id)
