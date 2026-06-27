"""SLA assignment service for tickets.

Resolves tickets to the explicit default ticket SLA policy.

Then creates/manages SLA clocks on the ticket lifecycle.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.tickets import Ticket, TicketStatus
from app.models.workflow import (
    SlaBreach,
    SlaBreachStatus,
    SlaClock,
    SlaClockStatus,
    SlaPolicy,
    WorkflowEntityType,
)

logger = logging.getLogger(__name__)

DEFAULT_TICKET_SLA_POLICY_NAME = "Ticket Resolution SLA"

CUSTOMER_AND_CABINET_TICKET_TYPES_24H = frozenset(
    {
        "customer link disconnection",
        "multiple customer link disconnection",
        "customer realignment",
        "cabinet disconnection",
        "multiple cabinet link disconnection",
        "multiple cabinet disconnection",
        "cabinet migration",
    }
)
CORE_LINK_TICKET_TYPES_48H = frozenset(
    {
        "core link disconnection",
        "multiple core link disconnection",
    }
)

# Ticket statuses that stop/complete SLA clocks
SLA_COMPLETE_STATUSES = frozenset(
    {
        TicketStatus.closed,
        TicketStatus.canceled,
        TicketStatus.merged,
    }
)

# Ticket statuses where SLA breach tracking is active.
SLA_APPLICABLE_STATUSES = frozenset(
    {
        TicketStatus.new,
        TicketStatus.open,
        TicketStatus.pending,
        TicketStatus.lastmile_rerun,
        TicketStatus.waiting_on_customer,
        TicketStatus.on_hold,
        TicketStatus.site_under_construction,
    }
)


def resolve_sla_policy(db: Session, ticket: Ticket) -> SlaPolicy | None:
    """Find the best matching SLA policy for a ticket.

    All support tickets use the explicit default ticket SLA policy.
    """
    policy = (
        db.query(SlaPolicy)
        .filter(SlaPolicy.entity_type == WorkflowEntityType.ticket)
        .filter(SlaPolicy.is_active.is_(True))
        .filter(func.lower(SlaPolicy.name) == DEFAULT_TICKET_SLA_POLICY_NAME.lower())
        .first()
    )
    if policy:
        return policy

    logger.warning(
        "ticket_sla_policy_not_found ticket_id=%s expected_policy_name=%s",
        getattr(ticket, "id", None),
        DEFAULT_TICKET_SLA_POLICY_NAME,
    )
    return None


def _normalize_ticket_type(ticket_type: str | None) -> str:
    return " ".join(str(ticket_type or "").strip().lower().split())


def ticket_type_sla_target_minutes(ticket_type: str | None) -> int | None:
    """Return fixed SLA target minutes for ticket types with explicit operational windows."""
    normalized = _normalize_ticket_type(ticket_type)
    if normalized in CUSTOMER_AND_CABINET_TICKET_TYPES_24H:
        return 24 * 60
    if normalized in CORE_LINK_TICKET_TYPES_48H:
        return 48 * 60
    return None


def smart_duration_label(started_at: datetime | None, ended_at: datetime | None = None) -> str | None:
    """Format an elapsed duration as mins, hrs/mins, or days/hrs/mins."""
    if not started_at:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    end_value = ended_at or datetime.now(UTC)
    if end_value.tzinfo is None:
        end_value = end_value.replace(tzinfo=UTC)
    total_minutes = max(int((end_value - started_at).total_seconds() // 60), 0)
    if total_minutes < 60:
        return f"{total_minutes} min" if total_minutes == 1 else f"{total_minutes} mins"
    if total_minutes < 1440:
        hours, minutes = divmod(total_minutes, 60)
        hour_label = "hr" if hours == 1 else "hrs"
        minute_label = "min" if minutes == 1 else "mins"
        return f"{hours} {hour_label} {minutes} {minute_label}"
    days, rem = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem, 60)
    day_label = "day" if days == 1 else "days"
    hour_label = "hr" if hours == 1 else "hrs"
    minute_label = "min" if minutes == 1 else "mins"
    return f"{days} {day_label} {hours} {hour_label} {minutes} {minute_label}"


def resolve_ticket_sla_target_minutes(
    db: Session,
    policy_id,
    priority: str | None,
    ticket_type: str | None,
) -> int | None:
    """Resolve ticket SLA target minutes from explicit ticket-type windows only."""
    return ticket_type_sla_target_minutes(ticket_type)


def ticket_sla_status(db: Session, ticket_id) -> dict | None:
    """Live SLA-clock summary for a ticket (or None if it has no clock).

    The read model behind the SLA-status API; time-to-breach is computed from the
    latest ticket clock rather than the stored breach records.
    """
    from app.services.common import coerce_uuid

    clock = (
        db.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.ticket)
        .filter(SlaClock.entity_id == coerce_uuid(ticket_id))
        .order_by(SlaClock.created_at.desc())
        .first()
    )
    if not clock:
        return None
    now = datetime.now(UTC)
    due_at = clock.due_at
    if due_at is not None and due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    breached = clock.status == SlaClockStatus.breached or clock.breached_at is not None
    minutes_remaining = int((due_at - now).total_seconds() // 60) if due_at is not None else None
    return {
        "status": clock.status.value if clock.status else None,
        "priority": clock.priority,
        "started_at": clock.started_at,
        "due_at": clock.due_at,
        "breached": breached,
        "breached_at": clock.breached_at,
        "minutes_remaining": minutes_remaining,
    }


def create_sla_clock_for_ticket(db: Session, ticket: Ticket) -> SlaClock | None:
    """Create an SLA clock for a newly created ticket.

    Finds the best policy, resolves target minutes by priority,
    and creates a running clock. Skips if no policy/target exists.
    """
    policy = resolve_sla_policy(db, ticket)
    if not policy:
        return None
    if ticket.status not in SLA_APPLICABLE_STATUSES:
        return None

    priority_value = ticket.priority.value if ticket.priority else None
    target_minutes = resolve_ticket_sla_target_minutes(db, policy.id, priority_value, ticket.ticket_type)
    if target_minutes is None:
        logger.warning(
            "ticket_sla_target_not_found ticket_id=%s policy_id=%s policy_name=%s priority=%s",
            ticket.id,
            policy.id,
            policy.name,
            priority_value,
        )
        return None

    # Check if clock already exists for this ticket + policy
    existing = (
        db.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.ticket)
        .filter(SlaClock.entity_id == ticket.id)
        .filter(SlaClock.policy_id == policy.id)
        .first()
    )
    if existing:
        return existing

    started_at = ticket.created_at or datetime.now(UTC)
    due_at = started_at + timedelta(minutes=target_minutes)
    clock = SlaClock(
        policy_id=policy.id,
        entity_type=WorkflowEntityType.ticket,
        entity_id=ticket.id,
        priority=priority_value,
        status=SlaClockStatus.running,
        started_at=started_at,
        due_at=due_at,
    )
    db.add(clock)
    return clock


def update_sla_clocks_for_status_change(
    db: Session,
    ticket: Ticket,
    old_status: TicketStatus | None,
    new_status: TicketStatus,
) -> None:
    """Update SLA clocks when a ticket's status changes.

    - Active statuses keep clocks running and eligible for breach
    - Exempt statuses complete clocks and resolve open breaches
    """
    clocks = (
        db.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.ticket)
        .filter(SlaClock.entity_id == ticket.id)
        .filter(SlaClock.status.in_([SlaClockStatus.running, SlaClockStatus.paused, SlaClockStatus.breached]))
        .all()
    )
    if not clocks:
        return

    now = datetime.now(UTC)

    for clock in clocks:
        if new_status in SLA_COMPLETE_STATUSES:
            clock.status = SlaClockStatus.completed
            clock.completed_at = now
            clock.paused_at = None
            open_breaches = (
                db.query(SlaBreach)
                .filter(SlaBreach.clock_id == clock.id)
                .filter(SlaBreach.status != SlaBreachStatus.resolved)
                .all()
            )
            for breach in open_breaches:
                breach.status = SlaBreachStatus.resolved

        elif new_status in SLA_APPLICABLE_STATUSES:
            clock.completed_at = None
            clock.paused_at = None
            if clock.status == SlaClockStatus.paused:
                clock.status = SlaClockStatus.running


def check_sla_breaches(db: Session, ticket_id) -> list[SlaClock]:
    """Check for SLA breaches on a ticket's running clocks.

    Returns list of newly breached clocks.
    """
    now = datetime.now(UTC)
    ticket = db.get(Ticket, ticket_id)
    if not ticket or ticket.status not in SLA_APPLICABLE_STATUSES:
        return []
    clocks = (
        db.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.ticket)
        .filter(SlaClock.entity_id == ticket_id)
        .filter(SlaClock.status == SlaClockStatus.running)
        .filter(SlaClock.due_at < now)
        .filter(SlaClock.breached_at.is_(None))
        .all()
    )

    breached = []
    for clock in clocks:
        due_at = clock.due_at if clock.due_at.tzinfo else clock.due_at.replace(tzinfo=UTC)
        clock.status = SlaClockStatus.breached
        clock.breached_at = due_at
        db.add(
            SlaBreach(
                clock_id=clock.id,
                status=SlaBreachStatus.open,
                breached_at=due_at,
            )
        )
        breached.append(clock)

    return breached
