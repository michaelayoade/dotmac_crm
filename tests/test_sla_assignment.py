"""Tests for SLA assignment service and ticket lifecycle integration."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.tickets import TicketPriority, TicketStatus
from app.models.workflow import (
    SlaBreach,
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
    ticket_type_sla_target_minutes,
    update_sla_clocks_for_status_change,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sla_policy(db_session):
    """Default SLA policy for tickets."""
    policy = SlaPolicy(
        name="Ticket Resolution SLA",
        entity_type=WorkflowEntityType.ticket,
        description="Default ticket SLA policy. Priority-driven resolution SLA for support tickets.",
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

    def test_ignores_ticket_type_policy_without_ticket_resolution_sla(self, db_session, ticket, sla_policy_by_type):
        ticket.ticket_type = "fiber_fault"
        db_session.commit()
        result = resolve_sla_policy(db_session, ticket)
        assert result is None

    def test_ignores_channel_policy_without_ticket_resolution_sla(self, db_session, ticket, sla_policy_by_channel):
        from app.models.tickets import TicketChannel

        ticket.channel = TicketChannel.email
        ticket.ticket_type = None
        db_session.commit()
        result = resolve_sla_policy(db_session, ticket)
        assert result is None

    def test_ignores_description_default_without_ticket_resolution_sla(self, db_session, ticket):
        policy = SlaPolicy(
            name="Default Ticket SLA",
            entity_type=WorkflowEntityType.ticket,
            description="default ticket response SLA",
            is_active=True,
        )
        db_session.add(policy)
        db_session.commit()

        result = resolve_sla_policy(db_session, ticket)
        assert result is None

    def test_prefers_ticket_resolution_sla_over_first_active_policy(self, db_session, ticket):
        wrong_policy = SlaPolicy(
            name="High Priority",
            entity_type=WorkflowEntityType.ticket,
            description="Policy for High priority Tickets",
            is_active=True,
        )
        default_policy = SlaPolicy(
            name="Ticket Resolution SLA",
            entity_type=WorkflowEntityType.ticket,
            description="Priority-driven resolution SLA for support tickets.",
            is_active=True,
        )
        db_session.add_all([wrong_policy, default_policy])
        db_session.commit()

        result = resolve_sla_policy(db_session, ticket)

        assert result.id == default_policy.id

    def test_returns_none_instead_of_first_policy_when_no_default(self, db_session, ticket, caplog):
        policy = SlaPolicy(
            name="High Priority",
            entity_type=WorkflowEntityType.ticket,
            description="Policy for High priority Tickets",
            is_active=True,
        )
        db_session.add(policy)
        db_session.commit()

        result = resolve_sla_policy(db_session, ticket)

        assert result is None
        assert "ticket_sla_policy_not_found" in caplog.text


# ---------------------------------------------------------------------------
# Clock creation
# ---------------------------------------------------------------------------


class TestTicketTypeSlaTargets:
    def test_customer_and_cabinet_ticket_types_use_24_hours(self):
        for ticket_type in [
            "Customer Link Disconnection",
            "Multiple Customer Link Disconnection",
            "Customer Realignment",
            "Cabinet Disconnection",
            "Multiple Cabinet Link Disconnection",
            "Multiple Cabinet Disconnection",
            "Cabinet Migration",
        ]:
            assert ticket_type_sla_target_minutes(ticket_type) == 1440

    def test_core_link_ticket_types_use_48_hours(self):
        for ticket_type in ["Core Link Disconnection", "Multiple Core Link Disconnection"]:
            assert ticket_type_sla_target_minutes(ticket_type) == 2880


class TestCreateSlaClock:
    def test_returns_none_for_ticket_type_without_explicit_sla_window(self, db_session, ticket, sla_policy):
        clock = create_sla_clock_for_ticket(db_session, ticket)
        assert clock is None

    def test_does_not_use_priority_specific_target_without_explicit_ticket_type(
        self, db_session, ticket, sla_policy, sla_target_urgent, sla_target_default
    ):
        ticket.priority = TicketPriority.urgent
        db_session.commit()

        clock = create_sla_clock_for_ticket(db_session, ticket)
        assert clock is None

    def test_no_duplicate_clocks(self, db_session, ticket, sla_policy, sla_target_default):
        ticket.ticket_type = "Customer Link Disconnection"
        db_session.commit()

        first = create_sla_clock_for_ticket(db_session, ticket)
        db_session.commit()
        second = create_sla_clock_for_ticket(db_session, ticket)
        assert first.id == second.id

    def test_returns_none_without_target(self, db_session, ticket, sla_policy):
        # Policy exists but no targets
        clock = create_sla_clock_for_ticket(db_session, ticket)
        assert clock is None

    def test_ignores_priority_target_case_insensitively_without_explicit_ticket_type(self, db_session, ticket):
        policy = SlaPolicy(
            name="Ticket Resolution SLA",
            entity_type=WorkflowEntityType.ticket,
            description="Priority-driven resolution SLA for support tickets.",
            is_active=True,
        )
        db_session.add(policy)
        db_session.flush()
        db_session.add(
            SlaTarget(
                policy_id=policy.id,
                priority="High",
                target_minutes=240,
                is_active=True,
            )
        )
        ticket.priority = TicketPriority.high
        db_session.commit()

        clock = create_sla_clock_for_ticket(db_session, ticket)
        assert clock is None

    def test_ticket_type_24_hour_target_overrides_priority_target(
        self, db_session, ticket, sla_policy, sla_target_urgent, sla_target_default
    ):
        ticket.priority = TicketPriority.urgent
        ticket.ticket_type = "Cabinet Disconnection"
        db_session.commit()

        clock = create_sla_clock_for_ticket(db_session, ticket)
        db_session.commit()

        assert clock is not None
        assert clock.due_at == clock.started_at + timedelta(hours=24)

    def test_core_link_ticket_type_uses_48_hour_target(
        self, db_session, ticket, sla_policy, sla_target_urgent, sla_target_default
    ):
        ticket.priority = TicketPriority.urgent
        ticket.ticket_type = "Core Link Disconnection"
        db_session.commit()

        clock = create_sla_clock_for_ticket(db_session, ticket)
        db_session.commit()

        assert clock is not None
        assert clock.due_at == clock.started_at + timedelta(hours=48)


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

    def test_waiting_status_keeps_sla_running(self, db_session, ticket, sla_policy):
        clock = self._create_running_clock(db_session, ticket, sla_policy)
        update_sla_clocks_for_status_change(db_session, ticket, TicketStatus.open, TicketStatus.waiting_on_customer)
        db_session.commit()
        db_session.refresh(clock)
        assert clock.status == SlaClockStatus.running
        assert clock.paused_at is None

    def test_on_hold_status_keeps_due_at_unchanged(self, db_session, ticket, sla_policy):
        clock = self._create_running_clock(db_session, ticket, sla_policy)
        original_due = clock.due_at
        update_sla_clocks_for_status_change(db_session, ticket, TicketStatus.open, TicketStatus.on_hold)
        db_session.commit()
        db_session.refresh(clock)
        assert clock.status == SlaClockStatus.running
        assert clock.paused_at is None
        assert clock.due_at == original_due

    def test_complete_on_closed(self, db_session, ticket, sla_policy):
        clock = self._create_running_clock(db_session, ticket, sla_policy)
        update_sla_clocks_for_status_change(db_session, ticket, TicketStatus.open, TicketStatus.closed)
        db_session.commit()
        db_session.refresh(clock)
        assert clock.status == SlaClockStatus.completed
        assert clock.completed_at is not None

    def test_late_close_completes_without_breach(self, db_session, ticket, sla_policy):
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
        assert clock.status == SlaClockStatus.completed
        assert clock.completed_at is not None

        breach = db_session.query(SlaBreach).filter(SlaBreach.clock_id == clock.id).first()
        assert breach is None


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
