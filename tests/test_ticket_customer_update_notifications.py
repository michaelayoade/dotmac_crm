from typing import ClassVar

from app.models.person import Person
from app.schemas.tickets import TicketCommentCreate
from app.services import tickets as tickets_service


def test_public_technician_comment_emits_customer_update_event(db_session, ticket, monkeypatch):
    technician = Person(first_name="Tech", last_name="One", email="tech-one@example.com")
    customer = Person(first_name="Customer", last_name="One", email="customer-one@example.com")
    db_session.add_all([technician, customer])
    db_session.commit()
    db_session.refresh(technician)
    db_session.refresh(customer)

    ticket.assigned_to_person_id = technician.id
    ticket.customer_person_id = customer.id
    db_session.commit()
    db_session.refresh(ticket)

    comment = tickets_service.ticket_comments.create(
        db_session,
        TicketCommentCreate(
            ticket_id=ticket.id,
            author_person_id=technician.id,
            body="We replaced the damaged connector and service is back up.",
            is_internal=False,
        ),
    )

    emitted: list[dict] = []

    class _Draft:
        meta: ClassVar[dict[str, str]] = {"insight_id": "ai-1"}
        update_message = "Our technician replaced a damaged connector and your service has been restored."

    monkeypatch.setattr(
        "app.services.ai.use_cases.ticket_customer_update.draft_customer_ticket_update",
        lambda *args, **kwargs: _Draft(),
    )

    def _capture_emit(db, event_type, payload, **kwargs):
        emitted.append({"event_type": event_type, "payload": payload, "kwargs": kwargs})
        return None

    monkeypatch.setattr("app.services.tickets.emit_event", _capture_emit)

    result = tickets_service.tickets.notify_customer_of_public_technician_comment(
        db_session,
        ticket_id=str(ticket.id),
        comment_id=str(comment.id),
        actor_person_id=str(technician.id),
    )

    assert result is not None
    assert emitted
    assert emitted[0]["event_type"].value == "ticket.customer_update"
    assert emitted[0]["payload"]["email"] == "customer-one@example.com"
    assert emitted[0]["payload"]["update_message"] == _Draft.update_message


def test_non_technician_comment_does_not_emit_customer_update_event(db_session, ticket, person, monkeypatch):
    customer = Person(first_name="Customer", last_name="Two", email="customer-two@example.com")
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)

    ticket.customer_person_id = customer.id
    db_session.commit()
    db_session.refresh(ticket)

    comment = tickets_service.ticket_comments.create(
        db_session,
        TicketCommentCreate(
            ticket_id=ticket.id,
            author_person_id=person.id,
            body="Checking on this ticket.",
            is_internal=False,
        ),
    )

    emitted: list[dict] = []
    monkeypatch.setattr("app.services.tickets.emit_event", lambda *args, **kwargs: emitted.append({"args": args}))

    result = tickets_service.tickets.notify_customer_of_public_technician_comment(
        db_session,
        ticket_id=str(ticket.id),
        comment_id=str(comment.id),
        actor_person_id=str(person.id),
    )

    assert result is None
    assert emitted == []
