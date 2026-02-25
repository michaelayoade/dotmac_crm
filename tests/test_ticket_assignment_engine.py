"""Tests for ticket rule-based assignment engine."""

from __future__ import annotations

import uuid

from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamType
from app.models.tickets import Ticket, TicketStatus
from app.models.workflow import TicketAssignmentRule, TicketAssignmentStrategy
from app.services.ticket_assignment.engine import auto_assign_ticket


def _person(db_session, first_name: str) -> Person:
    person = Person(
        first_name=first_name,
        last_name="Agent",
        email=f"{first_name.lower()}-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(person)
    db_session.flush()
    return person


def _ticket(db_session, *, title: str, service_team_id, region: str | None = None) -> Ticket:
    ticket = Ticket(title=title, service_team_id=service_team_id, region=region)
    db_session.add(ticket)
    db_session.flush()
    return ticket


def test_ticket_auto_assign_round_robin(db_session):
    team = ServiceTeam(name="Dispatch", team_type=ServiceTeamType.operations)
    db_session.add(team)
    db_session.flush()

    person_a = _person(db_session, "Alpha")
    person_b = _person(db_session, "Bravo")
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=person_a.id, is_active=True))
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=person_b.id, is_active=True))

    rule = TicketAssignmentRule(
        name="Ops default",
        priority=100,
        is_active=True,
        strategy=TicketAssignmentStrategy.round_robin,
        team_id=team.id,
    )
    db_session.add(rule)
    db_session.flush()

    t1 = _ticket(db_session, title="Ticket 1", service_team_id=team.id)
    t2 = _ticket(db_session, title="Ticket 2", service_team_id=team.id)
    db_session.commit()

    r1 = auto_assign_ticket(db_session, str(t1.id))
    r2 = auto_assign_ticket(db_session, str(t2.id))

    assert r1.assigned is True
    assert r2.assigned is True
    assert r1.assignee_person_id is not None
    assert r2.assignee_person_id is not None
    assert r1.assignee_person_id != r2.assignee_person_id


def test_ticket_auto_assign_least_loaded(db_session):
    team = ServiceTeam(name="Support", team_type=ServiceTeamType.support)
    db_session.add(team)
    db_session.flush()

    person_a = _person(db_session, "Charlie")
    person_b = _person(db_session, "Delta")
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=person_a.id, is_active=True))
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=person_b.id, is_active=True))

    # Seed one open ticket for person_a so least_loaded should pick person_b.
    existing = Ticket(
        title="Existing load",
        service_team_id=team.id,
        assigned_to_person_id=person_a.id,
        status=TicketStatus.open,
    )
    db_session.add(existing)

    rule = TicketAssignmentRule(
        name="Support least-loaded",
        priority=100,
        is_active=True,
        strategy=TicketAssignmentStrategy.least_loaded,
        team_id=team.id,
    )
    db_session.add(rule)
    candidate = _ticket(db_session, title="Needs assignment", service_team_id=team.id)
    db_session.commit()

    result = auto_assign_ticket(db_session, str(candidate.id))
    assert result.assigned is True
    assert result.assignee_person_id == str(person_b.id)


def test_ticket_auto_assign_respects_match_config(db_session):
    team = ServiceTeam(name="Regional", team_type=ServiceTeamType.field_service)
    db_session.add(team)
    db_session.flush()
    person = _person(db_session, "Echo")
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=person.id, is_active=True))

    rule = TicketAssignmentRule(
        name="North-only",
        priority=100,
        is_active=True,
        strategy=TicketAssignmentStrategy.round_robin,
        team_id=team.id,
        match_config={"regions": ["north"]},
    )
    db_session.add(rule)
    north = _ticket(db_session, title="North issue", service_team_id=team.id, region="north")
    south = _ticket(db_session, title="South issue", service_team_id=team.id, region="south")
    db_session.commit()

    north_result = auto_assign_ticket(db_session, str(north.id))
    south_result = auto_assign_ticket(db_session, str(south.id))

    assert north_result.assigned is True
    assert south_result.assigned is False
