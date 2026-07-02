"""Resolve → customer confirm / dispute / 24h auto-confirm (no false closures)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.tickets import TicketAccessToken, TicketStatus
from app.services.tickets import ticket_access_tokens, tickets


def test_confirmation_notice_queued_with_link(db_session, person):
    """The customer gets a notice carrying the confirm link (email fallback path)."""
    from app.models.notification import Notification
    from app.schemas.tickets import TicketCreate
    from app.services.tickets import tickets as tickets_service

    ticket = tickets_service.create(db_session, TicketCreate(title="No internet", customer_person_id=person.id))
    tickets_service.request_resolution_confirmation(db_session, str(ticket.id))

    notices = db_session.query(Notification).filter(Notification.recipient == person.email).all()
    assert len(notices) == 1
    body = notices[0].body or ""
    assert "/ticket-confirm/" in body  # the magic-link is in the message


def test_request_confirmation_sets_pending_not_closed(db_session, ticket):
    result = tickets.request_resolution_confirmation(db_session, str(ticket.id))
    assert result.status == TicketStatus.pending_confirmation
    assert result.resolved_at is not None
    assert result.closed_at is None  # NOT closed yet — awaiting the customer

    token = ticket_access_tokens.get_by_token(
        db_session,
        db_session.query(TicketAccessToken).filter(TicketAccessToken.ticket_id == ticket.id).one().token,
    )
    assert token is not None
    assert ticket_access_tokens.token_state(token) == "ok"


def test_confirm_closes_the_ticket(db_session, ticket):
    tickets.request_resolution_confirmation(db_session, str(ticket.id))
    token = db_session.query(TicketAccessToken).filter(TicketAccessToken.ticket_id == ticket.id).one()

    closed = tickets.confirm_resolution(db_session, token)
    assert closed.status == TicketStatus.closed
    assert closed.closed_at is not None
    assert (closed.metadata_ or {}).get("customer_confirmed_at")
    db_session.refresh(token)
    assert token.is_active is False  # link spent


def test_dispute_reopens_the_ticket(db_session, ticket):
    tickets.request_resolution_confirmation(db_session, str(ticket.id))
    token = db_session.query(TicketAccessToken).filter(TicketAccessToken.ticket_id == ticket.id).one()

    reopened = tickets.dispute_resolution(db_session, token, reason="Still down")
    assert reopened.status == TicketStatus.open
    assert reopened.resolved_at is None
    assert (reopened.metadata_ or {}).get("customer_dispute_reason") == "Still down"


def test_confirm_is_idempotent_when_already_closed(db_session, ticket):
    tickets.request_resolution_confirmation(db_session, str(ticket.id))
    token = db_session.query(TicketAccessToken).filter(TicketAccessToken.ticket_id == ticket.id).one()
    tickets.confirm_resolution(db_session, token)
    # A second confirm (e.g. double-click) must not error.
    again = tickets.confirm_resolution(db_session, token)
    assert again.status == TicketStatus.closed


def test_auto_confirm_closes_after_grace(db_session, ticket):
    tickets.request_resolution_confirmation(db_session, str(ticket.id), grace_hours=24)
    # Backdate the resolution past the grace window.
    ticket.resolved_at = datetime.now(UTC) - timedelta(hours=25)
    db_session.commit()

    count = tickets.auto_confirm_pending(db_session)
    assert count == 1
    db_session.refresh(ticket)
    assert ticket.status == TicketStatus.closed
    assert (ticket.metadata_ or {}).get("resolution_auto_confirmed") is True


def test_auto_confirm_skips_within_grace(db_session, ticket):
    tickets.request_resolution_confirmation(db_session, str(ticket.id), grace_hours=24)
    # Resolved just now — still inside the window.
    count = tickets.auto_confirm_pending(db_session)
    assert count == 0
    db_session.refresh(ticket)
    assert ticket.status == TicketStatus.pending_confirmation


def test_cannot_request_confirmation_on_closed_ticket(db_session, ticket):
    tickets.update  # noqa: B018 - ensure import path
    from app.schemas.tickets import TicketUpdate

    tickets.update(db_session, str(ticket.id), TicketUpdate(status=TicketStatus.closed, closed_at=datetime.now(UTC)))
    with pytest.raises(HTTPException) as exc:
        tickets.request_resolution_confirmation(db_session, str(ticket.id))
    assert exc.value.status_code == 409
