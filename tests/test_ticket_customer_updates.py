from __future__ import annotations

import base64
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models.dispatch import TechnicianProfile
from app.models.person import Person
from app.models.tickets import TicketStatus
from app.schemas.tickets import TicketCommentCreate, TicketCreate, TicketUpdate
from app.services import ticket_customer_updates as customer_updates
from app.services import tickets as tickets_service


def _make_person(db: Session, email: str, first_name: str = "Test", last_name: str = "User") -> Person:
    person = Person(first_name=first_name, last_name=last_name, email=email, is_active=True)
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


def _make_technician(db: Session, person: Person) -> None:
    db.add(TechnicianProfile(person_id=person.id, is_active=True))
    db.commit()


def test_status_change_sends_customer_email_with_ai_refinement(db_session: Session, monkeypatch):
    customer = _make_person(db_session, "customer-status@example.com", "Ada", "Customer")
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Fiber outage",
            customer_person_id=customer.id,
        ),
    )

    sent: list[dict] = []
    monkeypatch.setattr(
        customer_updates.ai_gateway,
        "generate_with_fallback",
        lambda *_args, **_kwargs: (
            SimpleNamespace(content="Dear subscriber, our team is currently working on your outage and will keep you updated."),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    monkeypatch.setattr(
        customer_updates,
        "send_email",
        lambda _db, to_email, subject, body_html, body_text, **kwargs: sent.append(
            {
                "to_email": to_email,
                "subject": subject,
                "body_html": body_html,
                "body_text": body_text,
                "attachments": kwargs.get("attachments"),
            }
        )
        or (True, None),
    )

    tickets_service.tickets.update(
        db_session,
        str(ticket.id),
        TicketUpdate(status=TicketStatus.pending),
    )

    assert len(sent) == 1
    assert sent[0]["to_email"] == "customer-status@example.com"
    assert "support ticket" in sent[0]["subject"].lower()
    assert "currently working on your outage" in sent[0]["body_text"]
    assert sent[0]["attachments"] is None


def test_technician_comment_sends_customer_email_with_attachments(db_session: Session, monkeypatch):
    customer = _make_person(db_session, "customer-comment@example.com", "Bola", "Customer")
    technician = _make_person(db_session, "tech@example.com", "Tobi", "Tech")
    _make_technician(db_session, technician)
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Cable fault",
            customer_person_id=customer.id,
        ),
    )

    sent: list[dict] = []
    monkeypatch.setattr(
        customer_updates.ai_gateway,
        "generate_with_fallback",
        lambda *_args, **_kwargs: (
            SimpleNamespace(content="Dear subscriber, there is a cable fault on your axis and restoration work is ongoing."),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    monkeypatch.setattr(customer_updates.storage, "get", lambda key: b"attachment-bytes")
    monkeypatch.setattr(
        customer_updates,
        "send_email",
        lambda _db, to_email, subject, body_html, body_text, **kwargs: sent.append(
            {
                "to_email": to_email,
                "subject": subject,
                "body_text": body_text,
                "attachments": kwargs.get("attachments") or [],
            }
        )
        or (True, None),
    )

    tickets_service.ticket_comments.create(
        db_session,
        TicketCommentCreate(
            ticket_id=ticket.id,
            author_person_id=technician.id,
            body="customer ticket don burn",
            attachments=[
                {
                    "key": "uploads/tickets/burn-report.pdf",
                    "file_name": "burn-report.pdf",
                    "mime_type": "application/pdf",
                }
            ],
        ),
    )

    assert len(sent) == 1
    assert sent[0]["to_email"] == "customer-comment@example.com"
    assert "cable fault" in sent[0]["body_text"].lower()
    assert len(sent[0]["attachments"]) == 1
    assert sent[0]["attachments"][0]["file_name"] == "burn-report.pdf"
    assert base64.b64decode(sent[0]["attachments"][0]["content_base64"]) == b"attachment-bytes"


def test_internal_technician_comment_does_not_send_customer_email(db_session: Session, monkeypatch):
    customer = _make_person(db_session, "customer-internal@example.com")
    technician = _make_person(db_session, "internal-tech@example.com")
    _make_technician(db_session, technician)
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="ONU offline",
            customer_person_id=customer.id,
        ),
    )

    sent: list[dict] = []
    monkeypatch.setattr(
        customer_updates,
        "send_email",
        lambda *_args, **_kwargs: sent.append({}) or (True, None),
    )

    tickets_service.ticket_comments.create(
        db_session,
        TicketCommentCreate(
            ticket_id=ticket.id,
            author_person_id=technician.id,
            body="Internal triage only",
            is_internal=True,
        ),
    )

    assert sent == []


def test_non_technician_comment_does_not_send_customer_email(db_session: Session, monkeypatch):
    customer = _make_person(db_session, "customer-nontech@example.com")
    non_technician = _make_person(db_session, "plain-user@example.com")
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Slow speeds",
            customer_person_id=customer.id,
        ),
    )

    sent: list[dict] = []
    monkeypatch.setattr(
        customer_updates,
        "send_email",
        lambda *_args, **_kwargs: sent.append({}) or (True, None),
    )

    tickets_service.ticket_comments.create(
        db_session,
        TicketCommentCreate(
            ticket_id=ticket.id,
            author_person_id=non_technician.id,
            body="Following up with customer",
        ),
    )

    assert sent == []
