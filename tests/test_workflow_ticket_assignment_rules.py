from __future__ import annotations

import uuid

from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamType
from app.models.tickets import Ticket
from app.models.workflow import TicketAssignmentStrategy
from app.schemas.workflow import (
    TicketAssignmentRuleCreate,
    TicketAssignmentRuleReorderRequest,
    TicketAssignmentRuleTestRequest,
    TicketAssignmentRuleUpdate,
)
from app.services import workflow as workflow_service


def _person(db_session, label: str) -> Person:
    person = Person(
        first_name=label,
        last_name="Agent",
        email=f"{label.lower()}-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(person)
    db_session.flush()
    return person


def test_ticket_assignment_rule_crud_reorder_and_soft_delete(db_session):
    rule_a = workflow_service.ticket_assignment_rules.create(
        db_session,
        TicketAssignmentRuleCreate(
            name="Rule A",
            priority=10,
            strategy=TicketAssignmentStrategy.round_robin,
        ),
    )
    rule_b = workflow_service.ticket_assignment_rules.create(
        db_session,
        TicketAssignmentRuleCreate(
            name="Rule B",
            priority=5,
            strategy=TicketAssignmentStrategy.least_loaded,
        ),
    )

    updated = workflow_service.ticket_assignment_rules.update(
        db_session,
        str(rule_a.id),
        TicketAssignmentRuleUpdate(name="Rule A Updated"),
    )
    assert updated.name == "Rule A Updated"

    reordered = workflow_service.ticket_assignment_rules.reorder(
        db_session,
        TicketAssignmentRuleReorderRequest(rule_ids=[rule_b.id, rule_a.id]),
    )
    assert [str(item.id) for item in reordered[:2]] == [str(rule_b.id), str(rule_a.id)]
    assert reordered[0].priority > reordered[1].priority

    workflow_service.ticket_assignment_rules.delete(db_session, str(rule_a.id))
    deleted = workflow_service.ticket_assignment_rules.get(db_session, str(rule_a.id))
    assert deleted.is_active is False

    active = workflow_service.ticket_assignment_rules.list(
        db_session,
        strategy=None,
        is_active=None,
        order_by="priority",
        order_dir="desc",
        limit=50,
        offset=0,
    )
    assert all(item.is_active for item in active)
    assert str(rule_a.id) not in {str(item.id) for item in active}


def test_ticket_assignment_rule_test_preview(db_session):
    team = ServiceTeam(name="Rule Team", team_type=ServiceTeamType.support)
    db_session.add(team)
    db_session.flush()

    p1 = _person(db_session, "Alpha")
    p2 = _person(db_session, "Bravo")
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=p1.id, is_active=True))
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=p2.id, is_active=True))

    rule = workflow_service.ticket_assignment_rules.create(
        db_session,
        TicketAssignmentRuleCreate(
            name="North Rule",
            priority=100,
            strategy=TicketAssignmentStrategy.round_robin,
            team_id=team.id,
            match_config={"regions": ["north"]},
        ),
    )
    matching_ticket = Ticket(title="North Ticket", region="north", service_team_id=team.id)
    non_matching_ticket = Ticket(title="South Ticket", region="south", service_team_id=team.id)
    db_session.add_all([matching_ticket, non_matching_ticket])
    db_session.commit()

    matched = workflow_service.ticket_assignment_rules.test_rule(
        db_session,
        str(rule.id),
        TicketAssignmentRuleTestRequest(ticket_ref=str(matching_ticket.id)),
    )
    assert matched["matched"] is True
    assert matched["candidate_count"] == 2
    assert matched["preview_assignee_person_id"] in {str(p1.id), str(p2.id)}

    not_matched = workflow_service.ticket_assignment_rules.test_rule(
        db_session,
        str(rule.id),
        TicketAssignmentRuleTestRequest(ticket_ref=str(non_matching_ticket.id)),
    )
    assert not_matched["matched"] is False
    assert not_matched["reason"] == "rule_not_matched"


def test_ticket_assignment_rule_test_preview_accepts_ticket_number(db_session):
    team = ServiceTeam(name="Rule Team Number", team_type=ServiceTeamType.support)
    db_session.add(team)
    db_session.flush()

    person = _person(db_session, "Number")
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=person.id, is_active=True))

    rule = workflow_service.ticket_assignment_rules.create(
        db_session,
        TicketAssignmentRuleCreate(
            name="Number Rule",
            priority=90,
            strategy=TicketAssignmentStrategy.round_robin,
            team_id=team.id,
            match_config={"regions": ["west"]},
        ),
    )
    ticket = Ticket(title="Number Ticket", number="TCK-1001", region="west", service_team_id=team.id)
    db_session.add(ticket)
    db_session.commit()

    matched = workflow_service.ticket_assignment_rules.test_rule(
        db_session,
        str(rule.id),
        TicketAssignmentRuleTestRequest(ticket_ref="TCK-1001"),
    )
    assert matched["matched"] is True
    assert matched["ticket_id"] == ticket.id
