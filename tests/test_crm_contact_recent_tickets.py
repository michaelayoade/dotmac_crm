from app.models.person import Person
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket, TicketPriority, TicketStatus
from app.services.crm.contacts.service import get_contact_recent_tickets


def test_contact_recent_tickets_include_customer_and_subscriber_linked_tickets(db_session):
    contact = Person(
        first_name="Jane",
        last_name="Customer",
        email="jane.customer@example.com",
    )
    creator = Person(
        first_name="Agent",
        last_name="User",
        email="agent.user@example.com",
    )
    db_session.add_all([contact, creator])
    db_session.flush()

    subscriber = Subscriber(
        person_id=contact.id,
        external_system="test",
        external_id="sub-001",
    )
    db_session.add(subscriber)
    db_session.flush()

    customer_ticket = Ticket(
        title="Customer-linked ticket",
        status=TicketStatus.open,
        priority=TicketPriority.medium,
        customer_person_id=contact.id,
        created_by_person_id=creator.id,
    )
    subscriber_ticket = Ticket(
        title="Subscriber-linked ticket",
        status=TicketStatus.pending,
        priority=TicketPriority.high,
        subscriber_id=subscriber.id,
        created_by_person_id=creator.id,
    )
    unrelated_ticket = Ticket(
        title="Unrelated ticket",
        status=TicketStatus.open,
        priority=TicketPriority.low,
        created_by_person_id=creator.id,
    )
    db_session.add_all([customer_ticket, subscriber_ticket, unrelated_ticket])
    db_session.commit()

    tickets = get_contact_recent_tickets(db_session, str(contact.id), subscriber_ids=None, limit=5)

    subjects = {ticket["subject"] for ticket in tickets}
    assert "Customer-linked ticket" in subjects
    assert "Subscriber-linked ticket" in subjects
    assert "Unrelated ticket" not in subjects


def test_contact_recent_tickets_use_real_ticket_reference(db_session):
    contact = Person(
        first_name="Jane",
        last_name="Customer",
        email="jane.customer.ref@example.com",
    )
    creator = Person(
        first_name="Agent",
        last_name="User",
        email="agent.user.ref@example.com",
    )
    db_session.add_all([contact, creator])
    db_session.flush()

    numbered_ticket = Ticket(
        title="Numbered ticket",
        number="TKT-2048",
        status=TicketStatus.open,
        priority=TicketPriority.medium,
        customer_person_id=contact.id,
        created_by_person_id=creator.id,
    )
    db_session.add(numbered_ticket)
    db_session.commit()

    tickets = get_contact_recent_tickets(db_session, str(contact.id), subscriber_ids=None, limit=5)

    assert tickets[0]["label"] == "TKT-2048"
    assert tickets[0]["href"] == "/admin/support/tickets/TKT-2048"
