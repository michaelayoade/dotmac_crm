"""Tests for ticket rule-based assignment engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.models.crm.enums import AgentPresenceStatus
from app.models.crm.presence import AgentPresence
from app.models.crm.team import CrmAgent
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.projects import Project, ProjectTask, ProjectTaskAssignee, ProjectType
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamType
from app.models.tickets import Ticket, TicketAssignee, TicketStatus
from app.models.workflow import TicketAssignmentRule, TicketAssignmentStrategy
from app.services.ticket_assignment.engine import auto_assign_project, auto_assign_ticket, auto_assign_ticket_all


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


def test_ticket_auto_assign_requires_presence_when_enabled(db_session, monkeypatch):
    def _resolve_value(_db, domain, key):
        if domain == SettingDomain.workflow and key == "ticket_auto_assign_require_presence":
            return True
        return None

    from app.services.ticket_assignment import selectors as selectors_module

    monkeypatch.setattr(selectors_module.settings_spec, "resolve_value", _resolve_value)

    team = ServiceTeam(name="Presence Team", team_type=ServiceTeamType.support)
    db_session.add(team)
    db_session.flush()

    offline_person = _person(db_session, "Offline")
    online_person = _person(db_session, "Online")
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=offline_person.id, is_active=True))
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=online_person.id, is_active=True))
    db_session.flush()

    offline_agent = CrmAgent(person_id=offline_person.id, is_active=True)
    online_agent = CrmAgent(person_id=online_person.id, is_active=True)
    db_session.add_all([offline_agent, online_agent])
    db_session.flush()
    db_session.add_all(
        [
            AgentPresence(
                agent_id=offline_agent.id,
                status=AgentPresenceStatus.offline,
                manual_override_status=AgentPresenceStatus.offline,
                last_seen_at=datetime.now(UTC),
            ),
            AgentPresence(
                agent_id=online_agent.id,
                status=AgentPresenceStatus.online,
                manual_override_status=None,
                last_seen_at=datetime.now(UTC),
            ),
        ]
    )

    rule = TicketAssignmentRule(
        name="Presence aware",
        priority=100,
        is_active=True,
        strategy=TicketAssignmentStrategy.round_robin,
        team_id=team.id,
    )
    db_session.add(rule)
    ticket = _ticket(db_session, title="Presence check", service_team_id=team.id)
    db_session.commit()

    result = auto_assign_ticket(db_session, str(ticket.id))

    assert result.assigned is True
    assert result.assignee_person_id == str(online_person.id)
    assert result.candidate_count == 1


def test_ticket_auto_assign_respects_max_open_ticket_limit(db_session, monkeypatch):
    def _resolve_value(_db, domain, key):
        if domain == SettingDomain.workflow and key == "ticket_auto_assign_max_open_tickets":
            return 0
        return None

    from app.services.ticket_assignment import selectors as selectors_module

    monkeypatch.setattr(selectors_module.settings_spec, "resolve_value", _resolve_value)

    team = ServiceTeam(name="Load Team", team_type=ServiceTeamType.operations)
    db_session.add(team)
    db_session.flush()

    loaded_person = _person(db_session, "Loaded")
    free_person = _person(db_session, "Free")
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=loaded_person.id, is_active=True))
    db_session.add(ServiceTeamMember(team_id=team.id, person_id=free_person.id, is_active=True))

    db_session.add(
        Ticket(
            title="Already open",
            service_team_id=team.id,
            assigned_to_person_id=loaded_person.id,
            status=TicketStatus.open,
        )
    )

    rule = TicketAssignmentRule(
        name="Capacity guard",
        priority=100,
        is_active=True,
        strategy=TicketAssignmentStrategy.round_robin,
        team_id=team.id,
    )
    db_session.add(rule)
    ticket = _ticket(db_session, title="Capacity check", service_team_id=team.id)
    db_session.commit()

    result = auto_assign_ticket(db_session, str(ticket.id))

    assert result.assigned is True
    assert result.assignee_person_id == str(free_person.id)
    assert result.candidate_count == 1


def test_ticket_auto_assign_applies_queue_fallback_team_when_no_candidates(db_session):
    rule_team = ServiceTeam(name="Fallback Team", team_type=ServiceTeamType.support)
    db_session.add(rule_team)
    db_session.flush()

    rule = TicketAssignmentRule(
        name="Queue fallback rule",
        priority=100,
        is_active=True,
        strategy=TicketAssignmentStrategy.round_robin,
        team_id=rule_team.id,
        match_config={"regions": ["fallback"]},
    )
    db_session.add(rule)

    ticket = Ticket(title="Needs queue fallback", region="fallback", service_team_id=None)
    db_session.add(ticket)
    db_session.commit()

    result = auto_assign_ticket(db_session, str(ticket.id))
    db_session.refresh(ticket)

    assert result.assigned is False
    assert result.reason == "queue_fallback_team_assigned"
    assert result.fallback_service_team_id == str(rule_team.id)
    assert ticket.service_team_id == rule_team.id
    assert ticket.assigned_to_person_id is None


def test_ticket_auto_assign_direct_technician_from_rule_config(db_session):
    person = _person(db_session, "Awwal")
    rule = TicketAssignmentRule(
        name="Air fiber ticket types",
        priority=100,
        is_active=True,
        match_config={
            "entity_types": ["ticket"],
            "ticket_types": ["Router Configuration"],
            "assignment_target": "technician",
            "assignee_person_id": str(person.id),
        },
    )
    db_session.add(rule)
    ticket = Ticket(title="Router setup", ticket_type="Router Configuration")
    db_session.add(ticket)
    db_session.commit()

    result = auto_assign_ticket(db_session, str(ticket.id))
    db_session.refresh(ticket)

    assert result.assigned is True
    assert result.strategy == "direct"
    assert result.assignment_target == "technician"
    assert ticket.assigned_to_person_id == person.id


def test_ticket_auto_assign_direct_technical_supervisor_from_rule_config(db_session):
    supervisor = _person(db_session, "Supervisor")
    rule = TicketAssignmentRule(
        name="Every ticket supervisor",
        priority=100,
        is_active=True,
        match_config={
            "entity_types": ["ticket"],
            "assignment_target": "technical_supervisor",
            "assignee_person_id": str(supervisor.id),
        },
    )
    db_session.add(rule)
    ticket = Ticket(title="Needs supervisor")
    db_session.add(ticket)
    db_session.commit()

    result = auto_assign_ticket(db_session, str(ticket.id))
    db_session.refresh(ticket)

    assert result.assigned is True
    assert result.assignment_target == "technical_supervisor"
    assert ticket.ticket_manager_person_id == supervisor.id


def test_ticket_auto_assign_applies_direct_technician_and_supervisor_rules(db_session):
    technician = _person(db_session, "Awwal")
    supervisor = _person(db_session, "Supervisor")
    db_session.add_all(
        [
            TicketAssignmentRule(
                name="Ticket technician",
                priority=200,
                is_active=True,
                match_config={
                    "entity_types": ["ticket"],
                    "ticket_types": ["Router Configuration"],
                    "assignment_target": "technician",
                    "assignee_person_id": str(technician.id),
                },
            ),
            TicketAssignmentRule(
                name="Ticket supervisor",
                priority=100,
                is_active=True,
                match_config={
                    "entity_types": ["ticket"],
                    "assignment_target": "technical_supervisor",
                    "assignee_person_id": str(supervisor.id),
                },
            ),
        ]
    )
    ticket = Ticket(title="Router setup", ticket_type="Router Configuration")
    db_session.add(ticket)
    db_session.commit()

    results = auto_assign_ticket_all(db_session, str(ticket.id))
    db_session.refresh(ticket)

    assert [result.assignment_target for result in results if result.assigned] == [
        "technician",
        "technical_supervisor",
    ]
    assert ticket.assigned_to_person_id == technician.id
    assert ticket.ticket_manager_person_id == supervisor.id


def test_ticket_auto_assign_applies_multiple_direct_technician_rules(db_session):
    first = _person(db_session, "FirstTech")
    second = _person(db_session, "SecondTech")
    db_session.add_all(
        [
            TicketAssignmentRule(
                name="Specific technician",
                priority=200,
                is_active=True,
                match_config={
                    "entity_types": ["ticket"],
                    "ticket_types": ["Router Configuration"],
                    "assignment_target": "technician",
                    "assignee_person_id": str(first.id),
                },
            ),
            TicketAssignmentRule(
                name="Default technician",
                priority=100,
                is_active=True,
                match_config={
                    "entity_types": ["ticket"],
                    "assignment_target": "technician",
                    "assignee_person_id": str(second.id),
                },
            ),
        ]
    )
    ticket = Ticket(title="Router setup", ticket_type="Router Configuration")
    db_session.add(ticket)
    db_session.commit()

    results = auto_assign_ticket_all(db_session, str(ticket.id))
    db_session.refresh(ticket)
    assignee_ids = {
        item[0]
        for item in db_session.query(TicketAssignee.person_id).filter(TicketAssignee.ticket_id == ticket.id).all()
    }

    assert [result.assignee_person_id for result in results if result.assigned] == [str(first.id), str(second.id)]
    assert ticket.assigned_to_person_id == first.id
    assert assignee_ids == {first.id, second.id}


def test_project_auto_assign_direct_technician_assigns_unassigned_tasks(db_session):
    technician = _person(db_session, "ProjectTech")
    rule = TicketAssignmentRule(
        name="Air fiber project technician",
        priority=100,
        is_active=True,
        match_config={
            "entity_types": ["project"],
            "project_types": ["air_fiber_installation"],
            "assignment_target": "technician",
            "assignee_person_id": str(technician.id),
        },
    )
    db_session.add(rule)
    project = Project(name="Air fiber install", project_type=ProjectType.air_fiber_installation)
    db_session.add(project)
    db_session.flush()
    task = ProjectTask(project_id=project.id, title="Install radio")
    db_session.add(task)
    db_session.commit()

    results = auto_assign_project(db_session, str(project.id))
    db_session.refresh(task)

    assert results[0].assigned is True
    assert results[0].assignment_target == "technician"
    assert task.assigned_to_person_id == technician.id


def test_project_auto_assign_applies_direct_technician_and_supervisor_rules(db_session):
    technician = _person(db_session, "ProjectTech")
    supervisor = _person(db_session, "ProjectSupervisor")
    db_session.add_all(
        [
            TicketAssignmentRule(
                name="Air fiber project technician",
                priority=200,
                is_active=True,
                match_config={
                    "entity_types": ["project"],
                    "project_types": ["air_fiber_installation"],
                    "assignment_target": "technician",
                    "assignee_person_id": str(technician.id),
                },
            ),
            TicketAssignmentRule(
                name="Every project supervisor",
                priority=100,
                is_active=True,
                match_config={
                    "entity_types": ["project"],
                    "assignment_target": "technical_supervisor",
                    "assignee_person_id": str(supervisor.id),
                },
            ),
        ]
    )
    project = Project(name="Air fiber install", project_type=ProjectType.air_fiber_installation)
    db_session.add(project)
    db_session.flush()
    task = ProjectTask(project_id=project.id, title="Install radio")
    db_session.add(task)
    db_session.commit()

    results = auto_assign_project(db_session, str(project.id))
    db_session.refresh(project)
    db_session.refresh(task)

    assert [result.assignment_target for result in results if result.assigned] == [
        "technician",
        "technical_supervisor",
    ]
    assert task.assigned_to_person_id == technician.id
    assert project.manager_person_id == supervisor.id
    assert project.project_manager_person_id == supervisor.id


def test_project_auto_assign_applies_multiple_direct_technician_rules(db_session):
    first = _person(db_session, "ProjectFirstTech")
    second = _person(db_session, "ProjectSecondTech")
    db_session.add_all(
        [
            TicketAssignmentRule(
                name="Air fiber project technician",
                priority=200,
                is_active=True,
                match_config={
                    "entity_types": ["project"],
                    "project_types": ["air_fiber_installation"],
                    "assignment_target": "technician",
                    "assignee_person_id": str(first.id),
                },
            ),
            TicketAssignmentRule(
                name="Every project technician",
                priority=100,
                is_active=True,
                match_config={
                    "entity_types": ["project"],
                    "assignment_target": "technician",
                    "assignee_person_id": str(second.id),
                },
            ),
        ]
    )
    project = Project(name="Air fiber install", project_type=ProjectType.air_fiber_installation)
    db_session.add(project)
    db_session.flush()
    task = ProjectTask(project_id=project.id, title="Install radio")
    db_session.add(task)
    db_session.commit()

    results = auto_assign_project(db_session, str(project.id))
    db_session.refresh(task)
    assignee_ids = {
        item[0]
        for item in db_session.query(ProjectTaskAssignee.person_id).filter(ProjectTaskAssignee.task_id == task.id).all()
    }

    assert [result.assignee_person_id for result in results if result.assigned] == [str(first.id), str(second.id)]
    assert task.assigned_to_person_id == first.id
    assert assignee_ids == {first.id, second.id}
