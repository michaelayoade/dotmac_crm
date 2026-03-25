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

_OPEN_STATUSES = {
    TicketStatus.new,
    TicketStatus.open,
    TicketStatus.pending,
    TicketStatus.waiting_on_customer,
    TicketStatus.lastmile_rerun,
    TicketStatus.site_under_construction,
    TicketStatus.on_hold,
}

_SUBSCRIBER_REQUIRED_TICKET_TYPES = {
    "bandwidth complaint",
    "customer link disconnection",
    "customer realignment",
    "dns/domain issue",
    "lan troubleshooting",
    "power optimization (if specific to customer premises)",
    "slow browsing / intermittent connectivity",
    "router troubleshooting",
    "router replacement",
    "call down support",
}

_BASE_STATION_REQUIRED_TICKET_TYPES = {
    "cedar view (likely a site/location issue)",
    "core link disconnection",
    "dell server down",
    "multiple cabinet disconnection",
    "multiple customer link disconnection",
    "nextcloud service down",
    "splynx server issue",
    "access point outage",
    "multiple cabinet link disconnection",
    "bts outage",
}


def ticket_type_requires_subscriber(ticket_type: str | None) -> bool:
    if not isinstance(ticket_type, str):
        return False
    return ticket_type.strip().lower() in _SUBSCRIBER_REQUIRED_TICKET_TYPES


def subscriber_required_ticket_types() -> list[str]:
    return sorted(_SUBSCRIBER_REQUIRED_TICKET_TYPES)


def ticket_type_requires_base_station(ticket_type: str | None) -> bool:
    if not isinstance(ticket_type, str):
        return False
    return ticket_type.strip().lower() in _BASE_STATION_REQUIRED_TICKET_TYPES


def base_station_required_ticket_types() -> list[str]:
    return sorted(_BASE_STATION_REQUIRED_TICKET_TYPES)


def validate_ticket_creation(db: Session, payload: TicketCreate) -> None:
    """Evaluate pre-creation rules and reject if a matching rule fires reject_creation."""
    if ticket_type_requires_subscriber(payload.ticket_type) and not payload.subscriber_id:
        raise HTTPException(status_code=400, detail="Subscriber is required for the selected ticket type.")
    metadata = payload.metadata_ if isinstance(payload.metadata_, dict) else {}
    base_station_details = str(metadata.get("base_station_details") or "").strip()
    if ticket_type_requires_base_station(payload.ticket_type) and not base_station_details:
        raise HTTPException(status_code=400, detail="Base station details are required for the selected ticket type.")

    rules: list[AutomationRule] = AutomationRulesManager.get_active_rules_for_event(
        db, EventType.ticket_pre_create.value
    )
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
