from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.tickets import Ticket, TicketComment, TicketSlaEvent
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def gather_ticket_context(db: Session, params: dict[str, Any]) -> str:
    ticket_id = params.get("ticket_id")
    if not ticket_id:
        raise ValueError("ticket_id is required")

    ticket = db.get(Ticket, coerce_uuid(ticket_id))
    if not ticket:
        raise ValueError("Ticket not found")

    max_comments = min(int(params.get("max_comments", 8)), 20)
    max_chars = int(params.get("max_chars_per_comment", 500))

    lines = [
        f"Ticket ID: {ticket.number or str(ticket.id)[:8]}",
        f"Title: {redact_text(ticket.title or '', max_chars=200)}",
        f"Status: {ticket.status.value}",
        f"Priority: {ticket.priority.value}",
        f"Channel: {ticket.channel.value}",
        f"Type: {ticket.ticket_type or 'unclassified'}",
        f"Created: {ticket.created_at.isoformat() if ticket.created_at else 'unknown'}",
        f"Updated: {ticket.updated_at.isoformat() if ticket.updated_at else 'unknown'}",
        f"Description: {redact_text(ticket.description or '', max_chars=800)}",
    ]

    if ticket.customer_person_id:
        customer = db.get(Person, ticket.customer_person_id)
        if customer:
            lines.append(f"Customer: {redact_text(customer.display_name or '', max_chars=100)}")

    if ticket.assigned_to_person_id:
        assignee = db.get(Person, ticket.assigned_to_person_id)
        if assignee:
            lines.append(f"Assigned to: {redact_text(assignee.display_name or '', max_chars=100)}")
    else:
        lines.append("Assigned to: UNASSIGNED")

    sla_events = (
        db.query(TicketSlaEvent)
        .filter(TicketSlaEvent.ticket_id == ticket.id)
        .order_by(TicketSlaEvent.created_at.desc())
        .limit(3)
        .all()
    )
    if sla_events:
        lines.append("SLA Events:")
        for ev in sla_events:
            lines.append(f"  - {ev.event_type}: {ev.created_at.isoformat() if ev.created_at else 'unknown'}")

    comments = (
        db.query(TicketComment)
        .filter(TicketComment.ticket_id == ticket.id)
        .order_by(TicketComment.created_at.desc())
        .limit(max(1, max_comments))
        .all()
    )
    comments = list(reversed(comments))
    if comments:
        lines.append("Recent comments:")
        for c in comments:
            prefix = "internal" if c.is_internal else "public"
            body = redact_text(c.body or "", max_chars=max_chars)
            if body:
                lines.append(f"  [{prefix}] {body}")

    return "\n".join(lines)
