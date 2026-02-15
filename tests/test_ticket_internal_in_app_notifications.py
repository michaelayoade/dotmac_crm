from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationChannel
from app.models.person import Person
from app.schemas.tickets import TicketCreate
from app.services import tickets as tickets_service


def test_ticket_created_in_app_notification_dedupes_roles_same_person(db_session: Session):
    p = Person(first_name="Role", last_name="Holder", email="roleholder-ticket@example.com")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)

    payload = TicketCreate(
        title="Link Down",
        region="Lagos",
        assigned_to_person_id=p.id,
        ticket_manager_person_id=p.id,
        assistant_manager_person_id=p.id,
    )

    ticket = tickets_service.tickets.create(db=db_session, payload=payload)

    notes = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == "roleholder-ticket@example.com")
        .filter(Notification.subject.like("%Link Down%"))
        .all()
    )
    assert len(notes) == 1
    assert notes[0].body
    assert "Technician" in notes[0].body
    assert "Ticket Manager" in notes[0].body
    assert "Site Project Coordinator" in notes[0].body
    assert "Site: Lagos" in notes[0].body
    assert "/admin/support/tickets/" in notes[0].body
    assert (ticket.number or str(ticket.id)) in notes[0].body


def test_ticket_created_does_not_broadcast_to_operations_role(db_session: Session):
    tech_ops = Person(first_name="Ops", last_name="Tech", email="ops-tech@example.com")
    ops_only = Person(first_name="Ops", last_name="Only", email="ops-only@example.com")
    db_session.add_all([tech_ops, ops_only])
    db_session.commit()
    db_session.refresh(tech_ops)
    db_session.refresh(ops_only)

    payload = TicketCreate(
        title="New Ticket Broadcast",
        region="Abuja",
        assigned_to_person_id=tech_ops.id,
    )
    tickets_service.tickets.create(db=db_session, payload=payload)

    tech_notes = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == "ops-tech@example.com")
        .filter(Notification.subject.like("%New Ticket Broadcast%"))
        .all()
    )
    assert len(tech_notes) == 1

    ops_notes = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == "ops-only@example.com")
        .filter(Notification.subject.like("%New Ticket Broadcast%"))
        .all()
    )
    assert len(ops_notes) == 0
