from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.models.person import Person
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket, TicketStatus
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def gather_customer_context(db: Session, params: dict[str, Any]) -> str:
    """
    Context builder for "customer success" analysis.

    Supports either:
      - subscriber_id
      - person_id
    """
    subscriber_id = params.get("subscriber_id")
    person_id = params.get("person_id")
    if not subscriber_id and not person_id:
        raise ValueError("subscriber_id or person_id is required")

    max_chars = int(params.get("max_chars", 700))
    lookback_days = max(1, min(int(params.get("lookback_days", 60)), 365))
    since = datetime.now(UTC) - timedelta(days=lookback_days)

    subscriber: Subscriber | None = None
    if subscriber_id:
        subscriber = db.get(Subscriber, coerce_uuid(subscriber_id))
        if not subscriber:
            raise ValueError("Subscriber not found")

    person: Person | None = None
    if person_id:
        person = db.get(Person, coerce_uuid(person_id))
        if not person:
            raise ValueError("Person not found")

    lines: list[str] = []
    if subscriber:
        subscriber_person: Person | None = None
        if subscriber.person_id:
            subscriber_person = db.get(Person, subscriber.person_id)
        lines.extend(
            [
                f"Subscriber ID: {str(subscriber.id)[:8]}",
                f"Subscriber status: {subscriber.status.value if hasattr(subscriber.status, 'value') else str(subscriber.status)}",
                f"Subscriber number: {redact_text(subscriber.subscriber_number or '', max_chars=80)}",
                f"Service name: {redact_text(subscriber.service_name or '', max_chars=200)}",
                f"Service plan: {redact_text(subscriber.service_plan or '', max_chars=160)}",
                f"Service address: {redact_text(subscriber.service_address or '', max_chars=220)}",
                f"Service region: {redact_text(subscriber.service_region or '', max_chars=80)}",
                f"Created: {subscriber.created_at.isoformat() if subscriber.created_at else 'unknown'}",
                f"Updated: {subscriber.updated_at.isoformat() if subscriber.updated_at else 'unknown'}",
            ]
        )
        if subscriber_person:
            lines.extend(
                [
                    f"Primary contact: {redact_text(subscriber_person.display_name or '', max_chars=140)}",
                    f"Primary contact email: {redact_text(subscriber_person.email or '', max_chars=120)}",
                    f"Primary contact phone: {redact_text(subscriber_person.phone or '', max_chars=120)}",
                ]
            )

    if person:
        lines.extend(
            [
                f"Person ID: {str(person.id)[:8]}",
                f"Person name: {redact_text(person.display_name or '', max_chars=140)}",
                f"Person email: {redact_text(person.email or '', max_chars=120)}",
                f"Person phone: {redact_text(person.phone or '', max_chars=120)}",
            ]
        )

    # Tickets summary
    ticket_q = db.query(Ticket).filter(Ticket.is_active.is_(True)).filter(Ticket.created_at >= since)
    if subscriber and hasattr(Ticket, "subscriber_id"):
        ticket_q = ticket_q.filter(Ticket.subscriber_id == subscriber.id)
    if person and hasattr(Ticket, "customer_person_id"):
        ticket_q = ticket_q.filter(Ticket.customer_person_id == person.id)

    total_tickets = (ticket_q.with_entities(func.count(Ticket.id)).scalar()) or 0
    open_tickets = (
        ticket_q.with_entities(func.count(Ticket.id))
        .filter(
            Ticket.status.in_(
                [
                    TicketStatus.new,
                    TicketStatus.open,
                    TicketStatus.pending,
                    TicketStatus.waiting_on_customer,
                    TicketStatus.on_hold,
                ]
            )
        )
        .scalar()
    ) or 0
    lines.append(f"Tickets (last {lookback_days}d): total={int(total_tickets)} open={int(open_tickets)}")

    recent_tickets = ticket_q.order_by(Ticket.updated_at.desc()).limit(5).all()
    if recent_tickets:
        lines.append("Recent tickets (sample):")
        for t in recent_tickets:
            title = redact_text(t.title or "", max_chars=200)
            desc = redact_text(t.description or "", max_chars=max_chars)
            lines.append(f"  - {t.number or str(t.id)[:8]} {t.status.value}: {title}. {desc}")

    # Inbox summary (if linked through CRM Person)
    if person:
        conv_q = (
            db.query(Conversation).filter(Conversation.person_id == person.id).filter(Conversation.created_at >= since)
        )
        conv_total = (conv_q.with_entities(func.count(Conversation.id)).scalar()) or 0
        lines.append(f"Conversations (last {lookback_days}d): total={int(conv_total)}")
        convs = conv_q.order_by(Conversation.updated_at.desc()).limit(5).all()
        if convs:
            lines.append("Recent conversations (sample):")
            for c in convs:
                lines.append(
                    f"  - {str(c.id)[:8]} status={c.status.value} subject={redact_text(c.subject or '', max_chars=200)}"
                )

    return "\n".join([line for line in lines if line.strip()])
