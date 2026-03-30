"""Tests for SLA assignment service and ticket lifecycle integration."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.tickets import TicketPriority, TicketStatus
from app.models.workflow import (
    SlaBreach,
    SlaBreachStatus,
    SlaClock,
    SlaClockStatus,
    SlaPolicy,
    SlaTarget,
    WorkflowEntityType,
)
from app.schemas.tickets import TicketCreate, TicketUpdate
from app.services import tickets as tickets_service
from app.services.sla_assignment import (
    check_sla_breaches,
    create_sla_clock_for_ticket,
    resolve_sla_policy,
    update_sla_clocks_for_status_change,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sla_policy(db_session):
    """Default SLA policy for tickets."""
    policy = SlaPolicy(
        name="Default Ticket SLA",
        entity_type=WorkflowEntityType.ticket,
        description="default ticket response SLA",
        is_active=True,
    )
    db_session.add(policy)
    db_session.commit()
    db_session.refresh(policy)
    return policy


@pytest.fixture()
def sla_policy_by_type(db_session):
    """SLA policy matching ticket type 'fiber_fault'."""
    policy = SlaPolicy(
        name="Fiber Fault SLA",
        entity_type=WorkflowEntityType.ticket,
        description="type:fiber_fault - 4hr response",
        is_active=True,
    )
    db_session.add(policy)
    db_session.commit()
    db_session.refresh(policy)
    return policy


@pytest.fixture()
def sla_policy_by_channel(db_session):
    """SLA policy matching channel 'email'."""
    policy = SlaPolicy(
        name="Email Channel SLA",
        entity_type=WorkflowEntityType.ticket,
        description="channel:email - standard response",
        is_active=True,
    )
    db_session.add(policy)
    db_session.commit()
    db_session.refresh(policy)
    return policy


@pytest.fixture()
def sla_target_urgent(db_session, sla_policy):
    """Urgent priority target: 60 min."""
    target = SlaTarget(
        policy_id=sla_policy.id,
        priority="urgent",
        target_minutes=60,
        warning_minutes=45,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()
    db_session.refresh(target)
    return target


@pytest.fixture()
def sla_target_default(db_session, sla_policy):
    """Default (null priority) target: 480 min."""
    target = SlaTarget(
        policy_id=sla_policy.id,
        priority=None,
        target_minutes=480,
        warning_minutes=360,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()
    db_session.refresh(target)
    return target


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------


class TestResolveSlaPolicy:
    def test_returns_none_when_no_policies(self, db_session, ticket):
        result = resolve_sla_policy(db_session, ticket)
        assert result is None

    def test_matches_by_ticket_type(self, db_session, ticket, sla_policy_by_type, sla_policy):
        ticket.ticket_type = "fiber_fault"
        db_session.commit()
        result = resolve_sla_policy(db_session, ticket)
        assert result.id == sla_policy_by_type.id

    def test_matches_by_channel(self, db_session, ticket, sla_policy_by_channel, sla_policy):
        from app.models.tickets import TicketChannel

        ticket.channel = TicketChannel.email
        ticket.ticket_type = None
        db_session.commit()
        result = resolve_sla_policy(db_session, ticket)
        assert result.id == sla_policy_by_channel.id

    def test_falls_back_to_default(self, db_session, ticket, sla_policy):
        result = resolve_sla_policy(db_session, ticket)
        assert result.id == sla_policy.id


# ---------------------------------------------------------------------------
# Clock creation
# ---------------------------------------------------------------------------


class TestCreateSlaClock:
    def test_creates_clock_with_correct_due_at(self, db_session, ticket, sla_policy, sla_target_default):
        clock = create_sla_clock_for_ticket(db_session, ticket)
        db_session.commit()
        assert clock is not None
        assert clock.status == SlaClockStatus.running
        assert clock.entity_id == ticket.id
        # due_at should be ~480 min from now
        expected_due = clock.started_at + timedelta(minutes=480)
        assert abs((clock.due_at - expected_due).total_seconds()) < 2

    def test_uses_priority_specific_target(self, db_session, ticket, sla_policy, sla_target_urgent, sla_target_default):
        ticket.priority = TicketPriority.urgent
        db_session.commit()
        clock = create_sla_clock_for_ticket(db_session, ticket)
        db_session.commit()
        assert clock is not None
        expected_due = clock.started_at + timedelta(minutes=60)
        assert abs((clock.due_at - expected_due).total_seconds()) < 2

    def test_no_duplicate_clocks(self, db_session, ticket, sla_policy, sla_target_default):
        first = create_sla_clock_for_ticket(db_session, ticket)
        db_session.commit()
        second = create_sla_clock_for_ticket(db_session, ticket)
        assert first.id == second.id

    def test_returns_none_without_target(self, db_session, ticket, sla_policy):
        # Policy exists but no targets
        clock = create_sla_clock_for_ticket(db_session, ticket)
        assert clock is None


# ---------------------------------------------------------------------------
# Status change handling
# ---------------------------------------------------------------------------


class TestStatusChangeClocks:
    def _create_running_clock(self, db_session, ticket, sla_policy, minutes=480):
        now = datetime.now(UTC)
        clock = SlaClock(
            policy_id=sla_policy.id,
            entity_type=WorkflowEntityType.ticket,
            entity_id=ticket.id,
            priority="normal",
            status=SlaClockStatus.running,
            started_at=now,
            due_at=now + timedelta(minutes=minutes),
        )
        db_session.add(clock)
        db_session.commit()
        db_session.refresh(clock)
        return clock

    def test_pause_on_waiting(self, db_session, ticket, sla_policy):
        clock = self._create_running_clock(db_session, ticket, sla_policy)
        update_sla_clocks_for_status_change(db_session, ticket, TicketStatus.open, TicketStatus.waiting_on_customer)
        db_session.commit()
        db_session.refresh(clock)
        assert clock.status == SlaClockStatus.paused
        assert clock.paused_at is not None

    def test_resume_on_reopen(self, db_session, ticket, sla_policy):
        clock = self._create_running_clock(db_session, ticket, sla_policy)
        original_due = clock.due_at
        # Pause
        update_sla_clocks_for_status_change(db_session, ticket, TicketStatus.open, TicketStatus.on_hold)
        db_session.commit()
        db_session.refresh(clock)
        # Resume
        update_sla_clocks_for_status_change(db_session, ticket, TicketStatus.on_hold, TicketStatus.open)
        db_session.commit()
        db_session.refresh(clock)
        assert clock.status == SlaClockStatus.running
        assert clock.paused_at is None
        # Due time should be extended by the pause duration
        assert clock.due_at >= original_due

    def test_complete_on_closed(self, db_session, ticket, sla_policy):
        clock = self._create_running_clock(db_session, ticket, sla_policy)
        update_sla_clocks_for_status_change(db_session, ticket, TicketStatus.open, TicketStatus.closed)
        db_session.commit()
        db_session.refresh(clock)
        assert clock.status == SlaClockStatus.completed
        assert clock.completed_at is not None

    def test_breach_on_late_close(self, db_session, ticket, sla_policy):
        # Create a clock that's already overdue
        now = datetime.now(UTC)
        clock = SlaClock(
            policy_id=sla_policy.id,
            entity_type=WorkflowEntityType.ticket,
            entity_id=ticket.id,
            priority="normal",
            status=SlaClockStatus.running,
            started_at=now - timedelta(hours=10),
            due_at=now - timedelta(hours=2),  # overdue
        )
        db_session.add(clock)
        db_session.commit()

        update_sla_clocks_for_status_change(db_session, ticket, TicketStatus.open, TicketStatus.closed)
        db_session.commit()
        db_session.refresh(clock)
        assert clock.status == SlaClockStatus.breached
        assert clock.breached_at is not None

        # Should also have a breach record
        breach = db_session.query(SlaBreach).filter(SlaBreach.clock_id == clock.id).first()
        assert breach is not None
        assert breach.status == SlaBreachStatus.open


# ---------------------------------------------------------------------------
# Breach checking
# ---------------------------------------------------------------------------


class TestCheckBreaches:
    def test_detects_overdue_running_clocks(self, db_session, ticket, sla_policy):
        now = datetime.now(UTC)
        clock = SlaClock(
            policy_id=sla_policy.id,
            entity_type=WorkflowEntityType.ticket,
            entity_id=ticket.id,
            priority="normal",
            status=SlaClockStatus.running,
            started_at=now - timedelta(hours=10),
            due_at=now - timedelta(hours=1),
        )
        db_session.add(clock)
        db_session.commit()

        breached = check_sla_breaches(db_session, ticket.id)
        db_session.commit()
        assert len(breached) == 1
        assert breached[0].status == SlaClockStatus.breached

    def test_ignores_already_breached(self, db_session, ticket, sla_policy):
        now = datetime.now(UTC)
        clock = SlaClock(
            policy_id=sla_policy.id,
            entity_type=WorkflowEntityType.ticket,
            entity_id=ticket.id,
            priority="normal",
            status=SlaClockStatus.running,
            started_at=now - timedelta(hours=10),
            due_at=now - timedelta(hours=1),
            breached_at=now - timedelta(minutes=30),
        )
        db_session.add(clock)
        db_session.commit()

        breached = check_sla_breaches(db_session, ticket.id)
        assert len(breached) == 0


class TestTicketRuntimeSlaChecks:
    def test_ticket_create_runs_runtime_breach_check(self, db_session, monkeypatch):
        called: list[object] = []

        def _capture(db, ticket_id):
            called.append(ticket_id)
            return []

        monkeypatch.setattr("app.services.sla_assignment.check_sla_breaches", _capture)

        tickets_service.tickets.create(db_session, TicketCreate(title="Runtime SLA create"))

        assert len(called) == 1

    def test_ticket_update_runs_runtime_breach_check_without_status_change(self, db_session, ticket, monkeypatch):
        called: list[object] = []

        def _capture(db, ticket_id):
            called.append(ticket_id)
            return []

        monkeypatch.setattr("app.services.sla_assignment.check_sla_breaches", _capture)

        tickets_service.tickets.update(
            db_session,
            str(ticket.id),
            TicketUpdate(title="Runtime SLA update"),
        )

        assert called == [ticket.id]
