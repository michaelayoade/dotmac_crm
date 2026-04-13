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
    SlaTarget,
    WorkflowEntityType,
)

logger = logging.getLogger(__name__)

DEFAULT_TICKET_SLA_POLICY_NAME = "Ticket Resolution SLA"

# Ticket statuses that pause SLA clocks (waiting on external input)
SLA_PAUSE_STATUSES = frozenset(
    {
        TicketStatus.waiting_on_customer,
        TicketStatus.on_hold,
        TicketStatus.site_under_construction,
    }
)

# Ticket statuses that stop/complete SLA clocks
SLA_COMPLETE_STATUSES = frozenset(
    {
        TicketStatus.closed,
        TicketStatus.canceled,
    }
)

# Ticket statuses where SLA clock should be running
SLA_RUNNING_STATUSES = frozenset(
    {
        TicketStatus.new,
        TicketStatus.open,
        TicketStatus.pending,
        TicketStatus.lastmile_rerun,
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


def _resolve_target(db: Session, policy_id, priority: str | None) -> SlaTarget | None:
    """Find matching SLA target by priority, with fallback to null-priority."""
    query = db.query(SlaTarget).filter(SlaTarget.policy_id == policy_id).filter(SlaTarget.is_active.is_(True))
    if priority:
        priority_key = priority.strip().lower()
        match = (
            query.filter(SlaTarget.priority.is_not(None)).filter(func.lower(SlaTarget.priority) == priority_key).first()
        )
        if match:
            return match
    return query.filter(SlaTarget.priority.is_(None)).first()


def create_sla_clock_for_ticket(db: Session, ticket: Ticket) -> SlaClock | None:
    """Create an SLA clock for a newly created ticket.

    Finds the best policy, resolves target minutes by priority,
    and creates a running clock. Skips if no policy/target exists.
    """
    policy = resolve_sla_policy(db, ticket)
    if not policy:
        return None

    priority_value = ticket.priority.value if ticket.priority else None
    target = _resolve_target(db, policy.id, priority_value)
    if not target:
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

    now = datetime.now(UTC)
    due_at = now + timedelta(minutes=target.target_minutes)
    clock = SlaClock(
        policy_id=policy.id,
        entity_type=WorkflowEntityType.ticket,
        entity_id=ticket.id,
        priority=priority_value,
        status=SlaClockStatus.running,
        started_at=now,
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

    - Pause on waiting/hold statuses
    - Resume on active statuses
    - Complete on resolved/closed
    - Check for breach on any transition
    """
    clocks = (
        db.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.ticket)
        .filter(SlaClock.entity_id == ticket.id)
        .filter(SlaClock.status.in_([SlaClockStatus.running, SlaClockStatus.paused]))
        .all()
    )
    if not clocks:
        return

    now = datetime.now(UTC)

    for clock in clocks:
        if new_status in SLA_COMPLETE_STATUSES:
            clock.status = SlaClockStatus.completed
            clock.completed_at = now
            # Check if completed after due → breach
            due_at = clock.due_at if clock.due_at.tzinfo else clock.due_at.replace(tzinfo=UTC)
            if now > due_at and not clock.breached_at:
                clock.status = SlaClockStatus.breached
                clock.breached_at = now
                db.add(
                    SlaBreach(
                        clock_id=clock.id,
                        status=SlaBreachStatus.open,
                        breached_at=now,
                    )
                )

        elif new_status in SLA_PAUSE_STATUSES:
            if clock.status == SlaClockStatus.running:
                clock.status = SlaClockStatus.paused
                clock.paused_at = now

        elif new_status in SLA_RUNNING_STATUSES:
            if clock.status == SlaClockStatus.paused and clock.paused_at:
                paused_at = clock.paused_at if clock.paused_at.tzinfo else clock.paused_at.replace(tzinfo=UTC)
                paused_seconds = int((now - paused_at).total_seconds())
                clock.total_paused_seconds = (clock.total_paused_seconds or 0) + paused_seconds
                due_at = clock.due_at if clock.due_at.tzinfo else clock.due_at.replace(tzinfo=UTC)
                clock.due_at = due_at + timedelta(seconds=paused_seconds)
                clock.status = SlaClockStatus.running
                clock.paused_at = None


def check_sla_breaches(db: Session, ticket_id) -> list[SlaClock]:
    """Check for SLA breaches on a ticket's running clocks.

    Returns list of newly breached clocks.
    """
    now = datetime.now(UTC)
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
        clock.status = SlaClockStatus.breached
        clock.breached_at = now
        db.add(
            SlaBreach(
                clock_id=clock.id,
                status=SlaBreachStatus.open,
                breached_at=now,
            )
        )
        breached.append(clock)

    return breached
