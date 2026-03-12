import uuid

from app.models.person import Person
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket
from app.queries.tickets import TicketQuery


def _unique_email() -> str:
    return f"ticket-search-{uuid.uuid4().hex[:12]}@example.com"


def _make_person(db, *, first_name="Test", last_name="User", display_name=None, email=None) -> Person:
    person = Person(
        first_name=first_name,
        last_name=last_name,
        display_name=display_name,
        email=email or _unique_email(),
    )
    db.add(person)
    db.flush()
    return person


def test_ticket_query_search_matches_customer_and_subscriber_fields(db_session):
    customer = _make_person(
        db_session,
        first_name="Alice",
        last_name="Anderson",
        display_name="Alice Anderson",
        email="alice.anderson@example.com",
    )
    subscriber_person = _make_person(
        db_session,
        first_name="Bob",
        last_name="Builder",
        display_name="Builder HQ",
        email="builder@example.com",
    )
    subscriber = Subscriber(person_id=subscriber_person.id, subscriber_number="SUB-9001")
    db_session.add(subscriber)
    db_session.flush()

    ticket = Ticket(
        title="Fiber issue",
        description="Intermittent packet loss",
        customer_person_id=customer.id,
        subscriber_id=subscriber.id,
    )
    db_session.add(ticket)
    db_session.commit()

    assert {row.id for row in TicketQuery(db_session).search("Alice").all()} == {ticket.id}
    assert {row.id for row in TicketQuery(db_session).search("alice.anderson@example.com").all()} == {ticket.id}
    assert {row.id for row in TicketQuery(db_session).search("Builder").all()} == {ticket.id}
    assert {row.id for row in TicketQuery(db_session).search("SUB-9001").all()} == {ticket.id}
    assert {row.id for row in TicketQuery(db_session).search(str(subscriber.id)).all()} == {ticket.id}
