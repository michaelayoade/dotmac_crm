"""Pre-creation ticket validation via automation rules.

Enriches the ticket creation context with duplicate/conflict data,
then evaluates automation rules for the ticket.pre_create event.
Raises HTTPException(409) if a rule triggers reject_creation.
"""

import logging
import time

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.automation_rule import AutomationLogOutcome, AutomationRule
from app.models.tickets import Ticket, TicketStatus
from app.schemas.tickets import TicketCreate
from app.services.automation_actions import CreationRejectedError, execute_actions
from app.services.automation_conditions import evaluate_conditions
from app.services.automation_rules import AutomationRulesManager
from app.services.events.types import Event, EventType

logger = logging.getLogger(__name__)

_OPEN_STATUSES = {TicketStatus.new, TicketStatus.open, TicketStatus.pending, TicketStatus.on_hold}


def validate_ticket_creation(db: Session, payload: TicketCreate) -> None:
    """Evaluate pre-creation rules and reject if a matching rule fires reject_creation."""
    rules: list[AutomationRule] = AutomationRulesManager.get_active_rules_for_event(db, EventType.ticket_pre_create.value)
    if not rules:
        return

    context = _build_context(db, payload)
    event = Event(
        event_type=EventType.ticket_pre_create,
        payload=context,
    )

    for rule in rules:
        if not evaluate_conditions(rule.conditions or [], context):
            continue

        start = time.monotonic()
        try:
            execute_actions(db, rule.actions or [], event)
        except CreationRejectedError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            AutomationRulesManager.record_execution(
                db,
                rule=rule,
                event_id=event.event_id,
                event_type=EventType.ticket_pre_create.value,
                outcome=AutomationLogOutcome.success,
                actions_executed=[{"action_type": "reject_creation", "success": True, "error": None}],
                duration_ms=duration_ms,
            )
            detail = exc.message
            if context.get("duplicate_ticket_id"):
                detail += f" (blocking ticket: {context['duplicate_ticket_id']})"
            raise HTTPException(status_code=409, detail=detail)

        duration_ms = int((time.monotonic() - start) * 1000)
        AutomationRulesManager.record_execution(
            db,
            rule=rule,
            event_id=event.event_id,
            event_type=EventType.ticket_pre_create.value,
            outcome=AutomationLogOutcome.success,
            actions_executed=[],
            duration_ms=duration_ms,
        )
        if rule.stop_after_match:
            break


def _build_context(db: Session, payload: TicketCreate) -> dict:
    """Build context dict for condition evaluation."""
    context: dict = {
        "ticket_type": payload.ticket_type,
        "customer_person_id": str(payload.customer_person_id) if payload.customer_person_id else None,
        "subscriber_id": str(payload.subscriber_id) if payload.subscriber_id else None,
        "priority": payload.priority.value if payload.priority else None,
        "channel": payload.channel.value if payload.channel else None,
        "title": payload.title,
    }

    if payload.customer_person_id:
        open_tickets = (
            db.query(Ticket)
            .filter(
                Ticket.customer_person_id == payload.customer_person_id,
                Ticket.status.in_(_OPEN_STATUSES),
            )
            .all()
        )

        open_types = [t.ticket_type for t in open_tickets if t.ticket_type]
        context["open_ticket_types"] = open_types
        context["open_ticket_count"] = len(open_tickets)

        if payload.ticket_type:
            duplicate = next(
                (t for t in open_tickets if t.ticket_type == payload.ticket_type),
                None,
            )
            if duplicate:
                context["duplicate_ticket_id"] = str(duplicate.id)

    return context
