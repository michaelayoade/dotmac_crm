from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamType
from app.models.tickets import TicketPriority, TicketStatus
from app.services.workqueue.providers.tickets import tickets_provider
from app.services.workqueue.scope import get_workqueue_scope
from app.services.workqueue.types import ActionKind, ItemKind, WorkqueueAudience


@pytest.fixture
def user():
    return SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"}, roles=set(), region="North")


def test_kind(user):
    assert tickets_provider.kind is ItemKind.ticket


def test_sla_breach(db_session, user, ticket_factory):
    ticket_factory(
        assignee_person_id=user.person_id,
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=10),
        region="North",
    )
    audience = WorkqueueAudience.self_
    items = tickets_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert len(items) == 1 and items[0].score == 100 and items[0].reason == "sla_breach"


def test_priority_urgent_open(db_session, user, ticket_factory):
    ticket_factory(
        assignee_person_id=user.person_id,
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        sla_due_at=None,
        region="North",
    )
    audience = WorkqueueAudience.self_
    items = tickets_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert len(items) == 1 and items[0].reason == "priority_urgent" and items[0].score == 80


def test_overdue_due_at(db_session, user, ticket_factory):
    ticket_factory(
        assignee_person_id=user.person_id,
        status=TicketStatus.open,
        due_at=datetime.now(UTC) - timedelta(hours=2),
        sla_due_at=None,
        region="North",
    )
    audience = WorkqueueAudience.self_
    items = tickets_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert items[0].reason == "overdue" and items[0].score == 70


def test_self_audience_includes_user_region_and_direct_assignments(db_session, user, ticket_factory):
    regional = ticket_factory(
        assignee_person_id=uuid4(),
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        sla_due_at=None,
        region=" north ",
    )
    assigned = ticket_factory(
        assignee_person_id=user.person_id,
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        sla_due_at=None,
        region="South",
    )
    audience = WorkqueueAudience.self_
    items = tickets_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert {item.item_id for item in items} == {regional.id, assigned.id}


def test_self_audience_without_user_region_keeps_direct_assignments(db_session, ticket_factory):
    user = SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"}, roles=set(), region=None)
    assigned = ticket_factory(
        assignee_person_id=user.person_id,
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        sla_due_at=None,
        region="North",
    )
    audience = WorkqueueAudience.self_
    items = tickets_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert {item.item_id for item in items} == {assigned.id}


def test_team_audience_includes_ticket_in_user_region(db_session, user, ticket_factory):
    matching = ticket_factory(
        assignee_person_id=None,
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        sla_due_at=None,
        region=" north ",
    )
    ticket_factory(
        assignee_person_id=None,
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        sla_due_at=None,
        region="South",
    )

    audience = WorkqueueAudience.team
    items = tickets_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )

    assert {item.item_id for item in items} == {matching.id}
    assert items[0].metadata["visibility_source"] == "person_region"


def test_claim_action_requires_claim_permission(db_session, user, ticket_factory):
    user.permissions = {"workqueue:view", "workqueue:audience:team"}
    ticket_factory(
        assignee_person_id=None,
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        sla_due_at=None,
        region="North",
    )

    audience = WorkqueueAudience.team
    items = tickets_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert ActionKind.claim not in items[0].actions

    user.permissions.add("workqueue:claim")
    items = tickets_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert ActionKind.claim in items[0].actions


def test_team_audience_includes_ticket_in_service_team_region(db_session, user, ticket_factory):
    user = SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"}, roles=set(), region=None)
    service_team = ServiceTeam(name="Garki Support", team_type=ServiceTeamType.support, region="Garki", is_active=True)
    db_session.add(service_team)
    db_session.commit()
    db_session.add(ServiceTeamMember(team_id=service_team.id, person_id=user.person_id, is_active=True))
    db_session.commit()
    matching = ticket_factory(
        assignee_person_id=None,
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        sla_due_at=None,
        region=" garki ",
    )
    ticket_factory(
        assignee_person_id=None,
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        sla_due_at=None,
        region="Wuse",
    )

    audience = WorkqueueAudience.team
    items = tickets_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )

    assert {item.item_id for item in items} == {matching.id}
    assert items[0].metadata["visibility_source"] == "service_team_region"


def test_audience_org_includes_others(db_session, user, ticket_factory):
    service_team = ServiceTeam(name="Support", team_type=ServiceTeamType.support, is_active=True)
    db_session.add(service_team)
    db_session.commit()
    ticket_factory(
        assignee_person_id=uuid4(),
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        service_team_id=service_team.id,
    )
    items = tickets_provider.fetch(
        db_session,
        user=user,
        audience=WorkqueueAudience.org,
        scope=get_workqueue_scope(db_session, user, WorkqueueAudience.org),
        snoozed_ids=set(),
    )
    assert len(items) == 1
