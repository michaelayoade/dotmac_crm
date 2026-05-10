from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.tickets import TicketPriority, TicketStatus
from app.services.workqueue.providers.tickets import tickets_provider
from app.services.workqueue.types import ItemKind, WorkqueueAudience


@pytest.fixture
def user():
    return SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"})


def test_kind(user):
    assert tickets_provider.kind is ItemKind.ticket


def test_sla_breach(db_session, user, ticket_factory):
    ticket_factory(
        assignee_person_id=user.person_id,
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    items = tickets_provider.fetch(
        db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set()
    )
    assert len(items) == 1 and items[0].score == 100 and items[0].reason == "sla_breach"


def test_priority_urgent_open(db_session, user, ticket_factory):
    ticket_factory(
        assignee_person_id=user.person_id,
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        sla_due_at=None,
    )
    items = tickets_provider.fetch(
        db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set()
    )
    assert len(items) == 1 and items[0].reason == "priority_urgent" and items[0].score == 80


def test_overdue_due_at(db_session, user, ticket_factory):
    ticket_factory(
        assignee_person_id=user.person_id,
        status=TicketStatus.open,
        due_at=datetime.now(UTC) - timedelta(hours=2),
        sla_due_at=None,
    )
    items = tickets_provider.fetch(
        db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set()
    )
    assert items[0].reason == "overdue" and items[0].score == 70


def test_audience_org_includes_others(db_session, user, ticket_factory):
    ticket_factory(assignee_person_id=uuid4(), status=TicketStatus.open, priority=TicketPriority.urgent)
    items = tickets_provider.fetch(
        db_session, user=user, audience=WorkqueueAudience.org, snoozed_ids=set()
    )
    assert len(items) == 1
