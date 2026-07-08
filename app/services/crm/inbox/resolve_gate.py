"""Resolve gate logic for CRM inbox.

When an agent resolves a conversation, this module checks whether the
conversation's person already has an active Lead.  If not, an interstitial
("gate") is shown so the agent can deliberately create a lead, link to an
existing contact, or skip lead creation entirely.

It also surfaces open support tickets for the conversation's contact so the
agent is nudged toward "resolved to ticket" (which tells the customer their
issue is still being worked on) instead of a plain resolve (which sends a
"successfully resolved" closing message).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.models.crm.sales import Lead
from app.models.tickets import Ticket
from app.services.common import coerce_uuid

HANDOFF_RESOLUTION_MODE = "ticket_handoff"


@dataclass(frozen=True)
class GateCheckResult:
    kind: Literal["needs_gate", "no_gate", "not_found"]


def check_resolve_gate(
    db: Session,
    conversation_id: str,
) -> GateCheckResult:
    """Check whether the resolve gate should be shown for a conversation.

    Returns ``needs_gate`` when the conversation's person has **no** active
    Lead record (``is_active=True``).  Returns ``no_gate`` when at least one
    active lead exists (including lost leads, since the agent already
    processed them).
    """
    try:
        conv_uuid = coerce_uuid(conversation_id)
    except Exception:
        return GateCheckResult(kind="not_found")

    conversation = db.get(Conversation, conv_uuid)
    if not conversation or not conversation.person_id:
        return GateCheckResult(kind="not_found")

    has_active_lead = (
        db.query(Lead.id).filter(Lead.person_id == conversation.person_id).filter(Lead.is_active.is_(True)).first()
    ) is not None

    if has_active_lead:
        return GateCheckResult(kind="no_gate")
    return GateCheckResult(kind="needs_gate")


def find_open_ticket_for_person(
    db: Session,
    *,
    person_id,
    conversation_id: str | None = None,
) -> Ticket | None:
    """Return the most recently updated non-terminal ticket for a contact.

    Matches tickets held directly (``customer_person_id``) or through any of
    the contact's subscriber accounts.
    """
    from sqlalchemy import or_

    from app.models.person import Person
    from app.services.crm.inbox.page_context import (
        _ACTIVE_TICKET_TERMINAL_STATUSES,
        _resolve_contact_subscriber_ids,
    )

    try:
        person_uuid = coerce_uuid(person_id)
    except Exception:
        return None
    contact = db.get(Person, person_uuid)
    if not contact:
        return None

    subscriber_ids = _resolve_contact_subscriber_ids(db, contact=contact, conversation_id=conversation_id)
    ticket_filters = [Ticket.customer_person_id == contact.id]
    if subscriber_ids:
        ticket_filters.append(Ticket.subscriber_id.in_(subscriber_ids))
    return (
        db.query(Ticket)
        .filter(Ticket.is_active.is_(True))
        .filter(Ticket.status.notin_(list(_ACTIVE_TICKET_TERMINAL_STATUSES)))
        .filter(or_(*ticket_filters))
        .order_by(Ticket.updated_at.desc())
        .first()
    )


def _ticket_belongs_to_conversation_contact(
    db: Session,
    *,
    conversation: Conversation,
    ticket: Ticket,
) -> bool:
    if not conversation.person_id:
        return False
    if ticket.customer_person_id == conversation.person_id:
        return True
    if not ticket.subscriber_id:
        return False

    from app.models.person import Person
    from app.services.crm.inbox.page_context import _resolve_contact_subscriber_ids

    contact = db.get(Person, conversation.person_id)
    if not contact:
        return False
    subscriber_ids = _resolve_contact_subscriber_ids(db, contact=contact, conversation_id=str(conversation.id))
    return ticket.subscriber_id in set(subscriber_ids)


def resolve_with_lead(
    db: Session,
    *,
    conversation_id: str,
    actor_id: str | None = None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> Literal["updated", "forbidden", "not_found", "error"]:
    """Create a Lead for the conversation's person, then resolve."""
    from app.schemas.crm.sales import LeadCreate
    from app.services.crm.inbox.conversation_status import update_conversation_status
    from app.services.crm.sales.service import leads

    try:
        conv_uuid = coerce_uuid(conversation_id)
    except Exception:
        return "not_found"

    conversation = db.get(Conversation, conv_uuid)
    if not conversation or not conversation.person_id:
        return "not_found"

    # Create the lead (Leads.create handles title, owner, currency defaults)
    leads.create(db, LeadCreate(person_id=conversation.person_id))

    result = update_conversation_status(
        db,
        conversation_id=conversation_id,
        new_status="resolved",
        actor_id=actor_id,
        roles=roles,
        scopes=scopes,
    )
    if result.kind == "forbidden":
        return "forbidden"
    if result.kind in ("not_found", "invalid_status", "invalid_transition"):
        return "error"
    return "updated"


def resolve_without_lead(
    db: Session,
    *,
    conversation_id: str,
    actor_id: str | None = None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> Literal["updated", "forbidden", "not_found", "error"]:
    """Resolve the conversation without creating a lead."""
    from app.services.crm.inbox.conversation_status import update_conversation_status

    result = update_conversation_status(
        db,
        conversation_id=conversation_id,
        new_status="resolved",
        actor_id=actor_id,
        roles=roles,
        scopes=scopes,
    )
    if result.kind == "forbidden":
        return "forbidden"
    if result.kind in ("not_found", "invalid_status", "invalid_transition"):
        return "error"
    return "updated"


def resolve_with_ticket_handoff(
    db: Session,
    *,
    conversation_id: str,
    actor_id: str | None = None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
    ticket_id: str | None = None,
) -> Literal["updated", "forbidden", "not_found", "error"]:
    """Resolve the conversation with ticket-handoff metadata.

    ``ticket_id`` links an open ticket that belongs to the conversation's
    contact when the conversation has no linked ticket yet.
    """
    from app.services.crm.inbox.conversation_status import ResolutionContext, update_conversation_status

    try:
        conv_uuid = coerce_uuid(conversation_id)
    except Exception:
        return "not_found"

    conversation = db.get(Conversation, conv_uuid)
    if not conversation:
        return "not_found"

    if not conversation.ticket_id and ticket_id:
        try:
            candidate = db.get(Ticket, coerce_uuid(ticket_id))
        except Exception:
            return "not_found"
        if not candidate or not candidate.is_active:
            return "not_found"
        if not _ticket_belongs_to_conversation_contact(db, conversation=conversation, ticket=candidate):
            return "not_found"
        conversation.ticket_id = candidate.id
        db.commit()

    if not conversation.ticket_id:
        return "not_found"

    ticket = db.get(Ticket, conversation.ticket_id)
    ticket_reference = ticket.number if ticket and ticket.number else str(conversation.ticket_id)
    result = update_conversation_status(
        db,
        conversation_id=conversation_id,
        new_status="resolved_to_ticket",
        actor_id=actor_id,
        roles=roles,
        scopes=scopes,
        resolution_context=ResolutionContext(
            mode=HANDOFF_RESOLUTION_MODE,
            label="Sent to ticket",
            ticket_id=str(conversation.ticket_id),
            ticket_reference=ticket_reference,
        ),
    )
    if result.kind == "forbidden":
        return "forbidden"
    if result.kind in ("not_found", "invalid_status", "invalid_transition"):
        return "error"
    return "updated"
