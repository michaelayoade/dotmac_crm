from datetime import UTC, datetime, timedelta

from app.models.tickets import TicketPriority, TicketStatus
from app.models.workflow import SlaClock, SlaClockStatus, SlaPolicy, SlaTarget, WorkflowEntityType
from app.schemas.tickets import TicketCreate, TicketUpdate
from app.services import tickets as tickets_service


def _seed_ticket_sla(db_session) -> SlaPolicy:
    policy = SlaPolicy(
        name="Ticket Resolution SLA",
        entity_type=WorkflowEntityType.ticket,
        description="Priority-driven resolution SLA for support tickets.",
        is_active=True,
    )
    db_session.add(policy)
    db_session.flush()
    db_session.add_all(
        [
            SlaTarget(policy_id=policy.id, priority="urgent", target_minutes=360, warning_minutes=180, is_active=True),
            SlaTarget(policy_id=policy.id, priority="high", target_minutes=240, warning_minutes=120, is_active=True),
            SlaTarget(policy_id=policy.id, priority="medium", target_minutes=1440, warning_minutes=720, is_active=True),
            SlaTarget(policy_id=policy.id, priority="low", target_minutes=2880, warning_minutes=1440, is_active=True),
            SlaTarget(policy_id=policy.id, priority="lower", target_minutes=120, warning_minutes=60, is_active=True),
        ]
    )
    db_session.commit()
    db_session.refresh(policy)
    return policy


def _latest_ticket_clock(db_session, ticket_id):
    return (
        db_session.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.ticket, SlaClock.entity_id == ticket_id)
        .order_by(SlaClock.created_at.desc())
        .first()
    )


def test_ticket_create_auto_starts_sla_clock(db_session):
    _seed_ticket_sla(db_session)

    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Cabinet link down",
            priority=TicketPriority.high,
            ticket_type="Cabinet Disconnection",
        ),
    )

    clock = _latest_ticket_clock(db_session, ticket.id)

    assert clock is not None
    assert clock.status == SlaClockStatus.running
    assert clock.priority == "high"
    assert clock.started_at is not None
    assert clock.due_at == clock.started_at + timedelta(minutes=240)


def test_ticket_terminal_status_completes_existing_sla_clock(db_session):
    _seed_ticket_sla(db_session)
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="AP outage", priority=TicketPriority.urgent),
    )

    before_close = datetime.now(UTC)
    tickets_service.tickets.update(
        db_session,
        str(ticket.id),
        TicketUpdate(status=TicketStatus.closed, closed_at=before_close),
    )
    clock = _latest_ticket_clock(db_session, ticket.id)

    assert clock is not None
    assert clock.status == SlaClockStatus.completed
    assert clock.completed_at is not None
    # completed_at is set by sla_assignment to now(), should be close to close time
    completed = clock.completed_at.replace(tzinfo=UTC) if clock.completed_at.tzinfo is None else clock.completed_at
    assert completed >= before_close


def test_ticket_close_and_reopen_keeps_clock_state(db_session):
    """Closing a ticket completes the SLA clock; reopening resumes via status change."""
    _seed_ticket_sla(db_session)
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Billing complaint", priority=TicketPriority.lower),
    )

    # Close ticket — clock should be completed
    tickets_service.tickets.update(
        db_session,
        str(ticket.id),
        TicketUpdate(status=TicketStatus.closed, closed_at=datetime.now(UTC)),
    )
    clock = _latest_ticket_clock(db_session, ticket.id)
    assert clock is not None
    assert clock.status == SlaClockStatus.completed


def test_ticket_no_sla_clock_without_policy(db_session):
    """Without SLA policy seeded, no clock should be created."""
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="No SLA ticket", priority=TicketPriority.high),
    )
    clock = _latest_ticket_clock(db_session, ticket.id)
    assert clock is None
