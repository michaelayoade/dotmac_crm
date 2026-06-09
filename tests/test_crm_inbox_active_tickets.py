from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationStatus
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamType
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket, TicketPriority, TicketStatus
from app.services.crm.inbox.page_context import build_inbox_contact_detail_context


def _ticket(
    db_session,
    *,
    title: str,
    customer_person_id=None,
    subscriber_id=None,
    status=TicketStatus.open,
    priority=TicketPriority.normal,
    service_team_id=None,
    assigned_to_person_id=None,
    created_at=None,
    updated_at=None,
):
    ticket = Ticket(
        title=title,
        customer_person_id=customer_person_id,
        subscriber_id=subscriber_id,
        status=status,
        priority=priority,
        service_team_id=service_team_id,
        assigned_to_person_id=assigned_to_person_id,
        created_at=created_at,
        updated_at=updated_at,
        is_active=True,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def test_inbox_contact_details_include_active_customer_tickets(db_session, person):
    team = ServiceTeam(name="NOC Team", team_type=ServiceTeamType.support, is_active=True)
    agent = Person(first_name="Ada", last_name="Agent", email="ada.agent@example.com")
    db_session.add_all([team, agent])
    db_session.commit()
    db_session.refresh(team)
    db_session.refresh(agent)

    active_ticket = _ticket(
        db_session,
        title="Router Offline",
        customer_person_id=person.id,
        status=TicketStatus.open,
        priority=TicketPriority.high,
        service_team_id=team.id,
        assigned_to_person_id=agent.id,
        created_at=datetime(2026, 6, 8, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 9, 9, 0, tzinfo=UTC),
    )
    _ticket(
        db_session,
        title="Closed ticket",
        customer_person_id=person.id,
        status=TicketStatus.closed,
        priority=TicketPriority.urgent,
    )

    context = build_inbox_contact_detail_context(
        db_session,
        contact_id=str(person.id),
        current_user={"permissions": ["support:ticket:read"], "roles": []},
    )

    tickets = context["contact"]["active_tickets"]
    assert [item["id"] for item in tickets] == [str(active_ticket.id)]
    assert tickets[0]["ticket_number"] == str(active_ticket.id)
    assert tickets[0]["subject"] == "Router Offline"
    assert tickets[0]["status"] == "open"
    assert tickets[0]["priority"] == "high"
    assert tickets[0]["assigned_team"] == "NOC Team"
    assert tickets[0]["assigned_user"] == "Ada Agent"
    assert tickets[0]["url"] == f"/admin/support/tickets/{active_ticket.id}"


def test_inbox_contact_details_resolve_tickets_from_conversation_subscriber(db_session, person):
    subscriber = Subscriber(person_id=person.id, subscriber_number="SUB-100", is_active=True)
    db_session.add(subscriber)
    db_session.flush()
    conversation = Conversation(
        person_id=person.id,
        status=ConversationStatus.open,
        metadata_={"subscriber_id": str(subscriber.id)},
    )
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(subscriber)
    db_session.refresh(conversation)

    ticket = _ticket(
        db_session,
        title="Fiber LOS",
        subscriber_id=subscriber.id,
        status=TicketStatus.pending,
        priority=TicketPriority.urgent,
    )

    context = build_inbox_contact_detail_context(
        db_session,
        contact_id=str(person.id),
        conversation_id=str(conversation.id),
        current_user={"permissions": ["support:ticket:read"], "roles": []},
    )

    assert [item["id"] for item in context["contact"]["active_tickets"]] == [str(ticket.id)]


def test_inbox_contact_active_tickets_accept_web_auth_scopes(db_session, person):
    ticket = _ticket(
        db_session,
        title="Router Offline",
        customer_person_id=person.id,
        status=TicketStatus.open,
        priority=TicketPriority.high,
    )

    context = build_inbox_contact_detail_context(
        db_session,
        contact_id=str(person.id),
        current_user={"roles": [], "scopes": ["support:ticket:read"]},
    )

    assert [item["id"] for item in context["contact"]["active_tickets"]] == [str(ticket.id)]


def test_inbox_contact_active_tickets_sort_and_limit(db_session, person):
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    expected = _ticket(
        db_session,
        title="Urgent ticket",
        customer_person_id=person.id,
        status=TicketStatus.pending,
        priority=TicketPriority.urgent,
        updated_at=now - timedelta(hours=3),
    )
    _ticket(
        db_session,
        title="High recent ticket",
        customer_person_id=person.id,
        status=TicketStatus.open,
        priority=TicketPriority.high,
        updated_at=now,
    )
    for index in range(12):
        _ticket(
            db_session,
            title=f"Normal ticket {index}",
            customer_person_id=person.id,
            status=TicketStatus.open,
            priority=TicketPriority.normal,
            updated_at=now - timedelta(minutes=index),
        )

    context = build_inbox_contact_detail_context(
        db_session,
        contact_id=str(person.id),
        current_user={"permissions": ["support:ticket:read"], "roles": []},
    )

    tickets = context["contact"]["active_tickets"]
    assert len(tickets) == 10
    assert tickets[0]["id"] == str(expected.id)
