from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.models.crm.enums import QuoteStatus
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.person import Person
from app.models.projects import Project
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamMemberRole, ServiceTeamType
from app.models.tickets import TicketStatus
from app.services.workqueue.aggregator import build_workqueue
from app.services.workqueue.types import ItemKind


def _user(person_id, *permissions, roles: list[str] | None = None):
    return SimpleNamespace(person_id=person_id, permissions=set(permissions), roles=set(roles or []))


def _person(db_session, *, first_name: str) -> Person:
    person = Person(first_name=first_name, last_name="WQ", email=f"{first_name.lower()}-{uuid4().hex[:8]}@example.com")
    db_session.add(person)
    db_session.flush()
    return person


def _service_team(db_session, *, name: str) -> ServiceTeam:
    team = ServiceTeam(name=name, team_type=ServiceTeamType.support, is_active=True)
    db_session.add(team)
    db_session.flush()
    return team


def _crm_team(db_session, *, service_team: ServiceTeam, name: str) -> CrmTeam:
    team = CrmTeam(name=name, service_team_id=service_team.id, is_active=True)
    db_session.add(team)
    db_session.flush()
    return team


def _link_service_team_member(
    db_session,
    *,
    service_team: ServiceTeam,
    person: Person,
    role: ServiceTeamMemberRole = ServiceTeamMemberRole.member,
) -> None:
    db_session.add(ServiceTeamMember(team_id=service_team.id, person_id=person.id, role=role, is_active=True))
    db_session.flush()


def _crm_agent(db_session, *, person: Person, crm_team: CrmTeam) -> CrmAgent:
    agent = CrmAgent(person_id=person.id, is_active=True, title="Agent")
    db_session.add(agent)
    db_session.flush()
    db_session.add(CrmAgentTeam(agent_id=agent.id, team_id=crm_team.id, is_active=True))
    db_session.flush()
    return agent


def _section_map(view):
    return {section.kind: section for section in view.sections}


def test_my_team_audience_scopes_all_modules_to_users_department(
    db_session,
    crm_conversation_factory,
    ticket_factory,
    lead_factory,
    quote_factory,
    project_task_factory,
):
    viewer = _person(db_session, first_name="Viewer")
    teammate = _person(db_session, first_name="Teammate")
    other = _person(db_session, first_name="Other")

    team_a = _service_team(db_session, name="Support A")
    team_b = _service_team(db_session, name="Support B")
    crm_team_a = _crm_team(db_session, service_team=team_a, name="CRM A")
    crm_team_b = _crm_team(db_session, service_team=team_b, name="CRM B")
    _link_service_team_member(db_session, service_team=team_a, person=viewer)
    _link_service_team_member(db_session, service_team=team_a, person=teammate)
    _link_service_team_member(db_session, service_team=team_b, person=other)
    db_session.commit()

    _crm_agent(db_session, person=viewer, crm_team=crm_team_a)
    _crm_agent(db_session, person=teammate, crm_team=crm_team_a)
    _crm_agent(db_session, person=other, crm_team=crm_team_b)
    db_session.commit()

    conv_a = crm_conversation_factory(assignment_team_id=crm_team_a.id)
    crm_conversation_factory(assignment_team_id=crm_team_b.id)
    ticket_a = ticket_factory(
        service_team_id=team_a.id,
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    ticket_factory(
        service_team_id=team_b.id,
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    lead_a = lead_factory(
        owner_person_id=teammate.id,
        owner_crm_team_id=crm_team_a.id,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
    )
    lead_factory(
        owner_person_id=other.id,
        owner_crm_team_id=crm_team_b.id,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
    )
    quote_a = quote_factory(
        owner_person_id=teammate.id, sent_at=datetime.now(UTC) - timedelta(days=8), status=QuoteStatus.sent
    )
    quote_factory(owner_person_id=other.id, sent_at=datetime.now(UTC) - timedelta(days=8), status=QuoteStatus.sent)
    task_a = project_task_factory(
        due_at=datetime.now(UTC) - timedelta(hours=1),
        service_team_id=team_a.id,
    )
    project_task_factory(
        due_at=datetime.now(UTC) - timedelta(hours=1),
        service_team_id=team_b.id,
    )

    view = build_workqueue(
        db_session, _user(viewer.id, "workqueue:view", "workqueue:audience:team"), requested_audience="team"
    )
    sections = _section_map(view)

    assert {item.item_id for item in sections[ItemKind.conversation].items} == {conv_a.id}
    assert {item.item_id for item in sections[ItemKind.ticket].items} == {ticket_a.id}
    assert {item.item_id for item in sections[ItemKind.lead].items} == {lead_a.id}
    assert {item.item_id for item in sections[ItemKind.quote].items} == {quote_a.id}
    assert {item.item_id for item in sections[ItemKind.task].items} == {task_a.id}


def test_team_audience_keeps_directly_assigned_conversation_without_team_mapping(
    db_session,
    crm_conversation_factory,
):
    viewer = _person(db_session, first_name="DirectConvViewer")
    other = _person(db_session, first_name="DirectConvOther")
    db_session.commit()

    direct_conv = crm_conversation_factory(
        assignee_person_id=viewer.id,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    crm_conversation_factory(
        assignee_person_id=other.id,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    view = build_workqueue(
        db_session,
        _user(viewer.id, "workqueue:view", "workqueue:audience:team"),
        requested_audience="team",
    )
    sections = _section_map(view)

    assert {item.item_id for item in sections[ItemKind.conversation].items} == {direct_conv.id}
    assert {item.metadata.get("visibility_source") for item in sections[ItemKind.conversation].items} == {
        "direct_assignment"
    }


def test_my_items_audience_only_returns_users_owned_records(
    db_session,
    crm_conversation_factory,
    ticket_factory,
    lead_factory,
    quote_factory,
    project_task_factory,
):
    viewer = _person(db_session, first_name="ViewerSelf")
    teammate = _person(db_session, first_name="TeammateSelf")
    team = _service_team(db_session, name="Support Self")
    crm_team = _crm_team(db_session, service_team=team, name="CRM Self")
    _link_service_team_member(db_session, service_team=team, person=viewer)
    _link_service_team_member(db_session, service_team=team, person=teammate)
    db_session.commit()

    _crm_agent(db_session, person=viewer, crm_team=crm_team)
    _crm_agent(db_session, person=teammate, crm_team=crm_team)
    db_session.commit()

    conv_self = crm_conversation_factory(assignee_person_id=viewer.id, assignment_team_id=crm_team.id)
    crm_conversation_factory(assignment_team_id=crm_team.id)
    crm_conversation_factory(assignee_person_id=teammate.id, assignment_team_id=crm_team.id)

    ticket_self = ticket_factory(
        assignee_person_id=viewer.id,
        service_team_id=team.id,
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    ticket_factory(
        assignee_person_id=teammate.id,
        service_team_id=team.id,
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    lead_self = lead_factory(
        owner_person_id=viewer.id,
        owner_crm_team_id=crm_team.id,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
    )
    lead_factory(
        owner_person_id=teammate.id,
        owner_crm_team_id=crm_team.id,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
    )

    quote_self = quote_factory(
        owner_person_id=viewer.id, sent_at=datetime.now(UTC) - timedelta(days=8), status=QuoteStatus.sent
    )
    quote_factory(owner_person_id=teammate.id, sent_at=datetime.now(UTC) - timedelta(days=8), status=QuoteStatus.sent)

    task_self = project_task_factory(
        assignee_person_id=viewer.id,
        due_at=datetime.now(UTC) - timedelta(hours=1),
        service_team_id=team.id,
    )
    project_task_factory(
        assignee_person_id=teammate.id,
        due_at=datetime.now(UTC) - timedelta(hours=1),
        service_team_id=team.id,
    )

    view = build_workqueue(
        db_session, _user(viewer.id, "workqueue:view", "workqueue:audience:team"), requested_audience="self"
    )
    sections = _section_map(view)

    assert {item.item_id for item in sections[ItemKind.conversation].items} == {conv_self.id}
    assert {item.item_id for item in sections[ItemKind.ticket].items} == {ticket_self.id}
    assert {item.item_id for item in sections[ItemKind.lead].items} == {lead_self.id}
    assert {item.item_id for item in sections[ItemKind.quote].items} == {quote_self.id}
    assert {item.item_id for item in sections[ItemKind.task].items} == {task_self.id}


def test_team_audience_keeps_directly_assigned_ticket_without_service_team_membership(
    db_session,
    ticket_factory,
):
    viewer = _person(db_session, first_name="DirectTicketViewer")
    outsider = _person(db_session, first_name="DirectTicketOutsider")
    team = _service_team(db_session, name="Regional Tickets")
    db_session.commit()

    direct_ticket = ticket_factory(
        assignee_person_id=viewer.id,
        service_team_id=team.id,
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    ticket_factory(
        assignee_person_id=outsider.id,
        service_team_id=team.id,
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    view = build_workqueue(
        db_session,
        _user(viewer.id, "workqueue:view", "workqueue:audience:team"),
        requested_audience="team",
    )
    sections = _section_map(view)

    assert {item.item_id for item in sections[ItemKind.ticket].items} == {direct_ticket.id}
    assert {item.metadata.get("visibility_source") for item in sections[ItemKind.ticket].items} == {"direct_assignment"}


def test_manager_team_audience_sees_managed_department_without_personal_assignment(
    db_session,
    crm_conversation_factory,
    ticket_factory,
    lead_factory,
    project_task_factory,
):
    manager = _person(db_session, first_name="Manager")
    teammate = _person(db_session, first_name="ManagedMember")
    team = _service_team(db_session, name="Managed Team")
    team.manager_person_id = manager.id
    crm_team = _crm_team(db_session, service_team=team, name="Managed CRM")
    _link_service_team_member(db_session, service_team=team, person=manager, role=ServiceTeamMemberRole.manager)
    _link_service_team_member(db_session, service_team=team, person=teammate)
    db_session.commit()

    _crm_agent(db_session, person=teammate, crm_team=crm_team)
    db_session.commit()

    crm_conversation_factory(assignment_team_id=crm_team.id)
    ticket_factory(
        service_team_id=team.id, status=TicketStatus.open, sla_due_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    lead_factory(
        owner_person_id=teammate.id,
        owner_crm_team_id=crm_team.id,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
    )
    project_task_factory(due_at=datetime.now(UTC) - timedelta(hours=1), service_team_id=team.id)

    view = build_workqueue(
        db_session, _user(manager.id, "workqueue:view", "workqueue:audience:team"), requested_audience="team"
    )
    sections = _section_map(view)

    assert sections[ItemKind.conversation].total == 1
    assert sections[ItemKind.ticket].total == 1
    assert sections[ItemKind.lead].total == 1
    assert sections[ItemKind.task].total == 1


def test_team_audience_shows_region_service_team_tickets_without_crm_agent_mapping(
    db_session,
    ticket_factory,
):
    viewer = _person(db_session, first_name="SpcViewer")
    teammate = _person(db_session, first_name="SpcMate")
    team = _service_team(db_session, name="SPC North")
    other_team = _service_team(db_session, name="SPC South")
    _link_service_team_member(db_session, service_team=team, person=viewer)
    _link_service_team_member(db_session, service_team=team, person=teammate)
    db_session.commit()

    visible_ticket = ticket_factory(
        service_team_id=team.id,
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    ticket_factory(
        service_team_id=other_team.id,
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    view = build_workqueue(
        db_session,
        _user(viewer.id, "workqueue:view", "workqueue:audience:team", roles=["spc"]),
        requested_audience="team",
    )
    sections = _section_map(view)

    assert {item.item_id for item in sections[ItemKind.ticket].items} == {visible_ticket.id}
    assert {item.metadata.get("visibility_source") for item in sections[ItemKind.ticket].items} == {
        "service_team_ownership"
    }


def test_team_audience_uses_profile_owned_leads_quotes_and_tasks_without_crm_team_link(
    db_session,
    lead_factory,
    quote_factory,
    project_task_factory,
):
    viewer = _person(db_session, first_name="ProfileViewer")
    teammate = _person(db_session, first_name="ProfileMate")
    outsider = _person(db_session, first_name="ProfileOther")
    team = _service_team(db_session, name="Profile Team")
    other_team = _service_team(db_session, name="Profile Other Team")
    _link_service_team_member(db_session, service_team=team, person=viewer)
    _link_service_team_member(db_session, service_team=team, person=teammate)
    _link_service_team_member(db_session, service_team=other_team, person=outsider)
    db_session.commit()

    db_session.add(CrmAgent(person_id=teammate.id, is_active=True, title="Sales"))
    db_session.add(CrmAgent(person_id=outsider.id, is_active=True, title="Sales"))
    db_session.commit()

    visible_lead = lead_factory(
        owner_person_id=teammate.id,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
    )
    lead_factory(
        owner_person_id=outsider.id,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
    )

    visible_quote = quote_factory(
        owner_person_id=teammate.id,
        sent_at=datetime.now(UTC) - timedelta(days=8),
        status=QuoteStatus.sent,
    )
    quote_factory(
        owner_person_id=outsider.id,
        sent_at=datetime.now(UTC) - timedelta(days=8),
        status=QuoteStatus.sent,
    )

    visible_task = project_task_factory(
        assignee_person_id=teammate.id,
        due_at=datetime.now(UTC) - timedelta(hours=1),
        service_team_id=team.id,
    )
    project_task_factory(
        assignee_person_id=outsider.id,
        due_at=datetime.now(UTC) - timedelta(hours=1),
        service_team_id=other_team.id,
    )

    view = build_workqueue(
        db_session,
        _user(viewer.id, "workqueue:view", "workqueue:audience:team"),
        requested_audience="team",
    )
    sections = _section_map(view)

    assert {item.item_id for item in sections[ItemKind.lead].items} == {visible_lead.id}
    assert {item.item_id for item in sections[ItemKind.quote].items} == {visible_quote.id}
    assert {item.item_id for item in sections[ItemKind.task].items} == {visible_task.id}
    assert {item.metadata.get("visibility_source") for item in sections[ItemKind.lead].items} == {"team_profile_owner"}
    assert {item.metadata.get("visibility_source") for item in sections[ItemKind.quote].items} == {"team_profile_owner"}
    assert {item.metadata.get("visibility_source") for item in sections[ItemKind.task].items} == {
        "team_profile_assignment"
    }


def test_org_audience_excludes_orphan_records_and_includes_owned_department_records(
    db_session,
    crm_conversation_factory,
    ticket_factory,
    lead_factory,
    quote_factory,
    project_task_factory,
):
    admin = _person(db_session, first_name="Admin")
    team_a = _service_team(db_session, name="Org Team A")
    team_b = _service_team(db_session, name="Org Team B")
    crm_team_a = _crm_team(db_session, service_team=team_a, name="Org CRM A")
    crm_team_b = _crm_team(db_session, service_team=team_b, name="Org CRM B")
    person_a = _person(db_session, first_name="OrgA")
    person_b = _person(db_session, first_name="OrgB")
    _link_service_team_member(db_session, service_team=team_a, person=person_a)
    _link_service_team_member(db_session, service_team=team_b, person=person_b)
    db_session.commit()

    _crm_agent(db_session, person=person_a, crm_team=crm_team_a)
    _crm_agent(db_session, person=person_b, crm_team=crm_team_b)
    db_session.commit()

    crm_conversation_factory(assignment_team_id=crm_team_a.id)
    crm_conversation_factory(assignment_team_id=crm_team_b.id)
    crm_conversation_factory()

    ticket_factory(
        service_team_id=team_a.id, status=TicketStatus.open, sla_due_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    ticket_factory(
        service_team_id=team_b.id, status=TicketStatus.open, sla_due_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    ticket_factory(status=TicketStatus.open, sla_due_at=datetime.now(UTC) - timedelta(minutes=5))

    lead_factory(
        owner_person_id=person_a.id,
        owner_crm_team_id=crm_team_a.id,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
    )
    lead_factory(
        owner_person_id=person_b.id,
        owner_crm_team_id=crm_team_b.id,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
    )
    lead_factory(next_action_at=datetime.now(UTC) - timedelta(hours=1))

    quote_factory(owner_person_id=person_a.id, sent_at=datetime.now(UTC) - timedelta(days=8), status=QuoteStatus.sent)
    quote_factory(owner_person_id=person_b.id, sent_at=datetime.now(UTC) - timedelta(days=8), status=QuoteStatus.sent)
    quote_factory(sent_at=datetime.now(UTC) - timedelta(days=8), status=QuoteStatus.sent)

    project_task_factory(due_at=datetime.now(UTC) - timedelta(hours=1), service_team_id=team_a.id)
    project_task_factory(due_at=datetime.now(UTC) - timedelta(hours=1), service_team_id=team_b.id)
    orphan_project = Project(name="Orphan Project")
    db_session.add(orphan_project)
    db_session.flush()
    project_task_factory(due_at=datetime.now(UTC) - timedelta(hours=1), project=orphan_project)

    view = build_workqueue(
        db_session,
        _user(admin.id, "workqueue:view", "workqueue:audience:team", "workqueue:audience:org", roles=["admin"]),
        requested_audience="org",
    )
    sections = _section_map(view)

    assert sections[ItemKind.conversation].total == 2
    assert sections[ItemKind.ticket].total == 2
    assert sections[ItemKind.lead].total == 2
    assert sections[ItemKind.quote].total == 2
    assert sections[ItemKind.task].total == 2
