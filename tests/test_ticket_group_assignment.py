from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationChannel
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamMemberRole, ServiceTeamType
from app.queries.tickets import TicketQuery
from app.schemas.tickets import TicketCreate, TicketUpdate
from app.services import tickets as tickets_service


def _make_person(db: Session, email: str) -> Person:
    person = Person(first_name="Group", last_name="Member", email=email, is_active=True)
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


def _make_team(db: Session, name: str = "Support Team") -> ServiceTeam:
    team = ServiceTeam(name=name, team_type=ServiceTeamType.support, is_active=True)
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _add_member(db: Session, team: ServiceTeam, person: Person) -> None:
    db.add(
        ServiceTeamMember(
            team_id=team.id,
            person_id=person.id,
            role=ServiceTeamMemberRole.member,
            is_active=True,
        )
    )
    db.commit()


def test_ticket_group_assignment_create_notifies_all_members_push_and_email(db_session: Session):
    team = _make_team(db_session, "NOC Team")
    p1 = _make_person(db_session, "noc-1@example.com")
    p2 = _make_person(db_session, "noc-2@example.com")
    _add_member(db_session, team, p1)
    _add_member(db_session, team, p2)

    tickets_service.tickets.create(
        db=db_session,
        payload=TicketCreate(
            title="Backbone Link Flap",
            service_team_id=team.id,
            region="Lagos",
        ),
    )

    for email in ["noc-1@example.com", "noc-2@example.com"]:
        push_rows = (
            db_session.query(Notification)
            .filter(Notification.channel == NotificationChannel.push)
            .filter(Notification.recipient == email)
            .filter(Notification.subject.like("%Backbone Link Flap%"))
            .filter(Notification.body.like("%assigned to your group%"))
            .all()
        )
        email_rows = (
            db_session.query(Notification)
            .filter(Notification.channel == NotificationChannel.email)
            .filter(Notification.recipient == email)
            .filter(Notification.subject.like("%Backbone Link Flap%"))
            .filter(Notification.body.like("%assigned to your group%"))
            .all()
        )
        assert len(push_rows) == 1
        assert len(email_rows) == 1


def test_ticket_group_assignment_update_notifies_new_group_members(db_session: Session):
    team = _make_team(db_session, "Field Ops")
    member = _make_person(db_session, "field-ops@example.com")
    _add_member(db_session, team, member)

    ticket = tickets_service.tickets.create(
        db=db_session,
        payload=TicketCreate(
            title="ONU Offline",
            region="Abuja",
        ),
    )

    tickets_service.tickets.update(
        db=db_session,
        ticket_id=str(ticket.id),
        payload=TicketUpdate(service_team_id=team.id),
    )

    push_rows = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == "field-ops@example.com")
        .filter(Notification.subject.like("%ONU Offline%"))
        .filter(Notification.body.like("%assigned to your group%"))
        .all()
    )
    email_rows = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.email)
        .filter(Notification.recipient == "field-ops@example.com")
        .filter(Notification.subject.like("%ONU Offline%"))
        .filter(Notification.body.like("%assigned to your group%"))
        .all()
    )
    assert len(push_rows) == 1
    assert len(email_rows) == 1


def test_ticket_query_assigned_to_me_includes_service_team_memberships(db_session: Session):
    team = _make_team(db_session, "Support Alpha")
    member = _make_person(db_session, "team-member@example.com")
    outsider = _make_person(db_session, "outsider@example.com")
    _add_member(db_session, team, member)

    ticket = tickets_service.tickets.create(
        db=db_session,
        payload=TicketCreate(
            title="Customer PPPoE Down",
            service_team_id=team.id,
        ),
    )

    member_results = (
        TicketQuery(db_session)
        .by_assigned_to_or_team_member(member.id)
        .active_only()
        .all()
    )
    outsider_results = (
        TicketQuery(db_session)
        .by_assigned_to_or_team_member(outsider.id)
        .active_only()
        .all()
    )

    member_ids = {str(row.id) for row in member_results}
    outsider_ids = {str(row.id) for row in outsider_results}
    assert str(ticket.id) in member_ids
    assert str(ticket.id) not in outsider_ids


def test_ticket_service_list_assigned_filter_includes_service_team_memberships(db_session: Session):
    team = _make_team(db_session, "Support Beta")
    member = _make_person(db_session, "svc-member@example.com")
    outsider = _make_person(db_session, "svc-outsider@example.com")
    _add_member(db_session, team, member)

    ticket = tickets_service.tickets.create(
        db=db_session,
        payload=TicketCreate(
            title="Core Router Alarm",
            service_team_id=team.id,
        ),
    )

    member_results = tickets_service.tickets.list(
        db=db_session,
        subscriber_id=None,
        status=None,
        priority=None,
        channel=None,
        search=None,
        created_by_person_id=None,
        assigned_to_person_id=str(member.id),
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=50,
        offset=0,
        filters_payload=None,
    )
    outsider_results = tickets_service.tickets.list(
        db=db_session,
        subscriber_id=None,
        status=None,
        priority=None,
        channel=None,
        search=None,
        created_by_person_id=None,
        assigned_to_person_id=str(outsider.id),
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=50,
        offset=0,
        filters_payload=None,
    )

    member_ids = {str(row.id) for row in member_results}
    outsider_ids = {str(row.id) for row in outsider_results}
    assert str(ticket.id) in member_ids
    assert str(ticket.id) not in outsider_ids
