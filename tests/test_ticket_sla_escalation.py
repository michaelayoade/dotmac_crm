"""Ticket SLA escalation: priority-based default targets + real-time breach
notification/escalation (parity with project-task breaches)."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.notification import Notification, NotificationChannel
from app.models.person import Person
from app.models.tickets import Ticket, TicketPriority, TicketStatus
from app.models.workflow import SlaClock, SlaClockStatus, SlaPolicy, WorkflowEntityType
from app.services import sla_assignment
from app.services import tickets as tickets_service
from app.services.events.types import EventType

# ── helpers ──────────────────────────────────────────────────────────────────


def _person(db, email):
    p = Person(first_name="Mel", last_name="Gee", email=email)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _ticket(db, **kw):
    t = Ticket(title="Connectivity issue", **kw)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _ticket_policy(db, name="Ticket Resolution SLA"):
    policy = SlaPolicy(name=name, entity_type=WorkflowEntityType.ticket, is_active=True)
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


def _breached_clock(db, *, entity_id, policy, entity_type=WorkflowEntityType.ticket):
    now = datetime.now(UTC)
    clock = SlaClock(
        policy_id=policy.id,
        entity_type=entity_type,
        entity_id=entity_id,
        status=SlaClockStatus.breached,
        started_at=now - timedelta(hours=2),
        due_at=now - timedelta(hours=1),
        breached_at=now - timedelta(hours=1),
    )
    db.add(clock)
    db.commit()
    db.refresh(clock)
    return clock


# ── priority-based default targets (Part A) ──────────────────────────────────


def test_type_window_takes_precedence_over_priority(db_session):
    minutes = sla_assignment.resolve_ticket_sla_target_minutes(db_session, None, "low", "core link disconnection")
    assert minutes == 48 * 60  # the type window wins, not the priority default


def test_priority_default_off_by_default(db_session):
    # Opt-in: without the setting, priority does NOT yield a target — preserving
    # the historical "explicit infra types only" policy.
    assert sla_assignment.resolve_ticket_sla_target_minutes(db_session, None, "urgent", "general question") is None


def _enable_priority_defaults(monkeypatch):
    monkeypatch.setattr(
        "app.services.settings_spec.resolve_value",
        lambda db, domain, key: key == "ticket_sla_priority_defaults_enabled",
    )


def test_priority_default_applies_when_enabled(db_session, monkeypatch):
    _enable_priority_defaults(monkeypatch)
    assert sla_assignment.resolve_ticket_sla_target_minutes(db_session, None, "urgent", None) == 4 * 60
    assert sla_assignment.resolve_ticket_sla_target_minutes(db_session, None, "normal", None) == 24 * 60
    assert sla_assignment.resolve_ticket_sla_target_minutes(db_session, None, "lower", None) == 72 * 60


def test_no_target_without_priority_or_type(db_session):
    assert sla_assignment.resolve_ticket_sla_target_minutes(db_session, None, None, None) is None


def test_ordinary_ticket_gets_clock_when_priority_defaults_enabled(db_session, monkeypatch):
    _enable_priority_defaults(monkeypatch)
    _ticket_policy(db_session)
    ticket = _ticket(db_session, priority=TicketPriority.normal, status=TicketStatus.open)
    clock = sla_assignment.create_sla_clock_for_ticket(db_session, ticket)
    assert clock is not None
    assert clock.entity_type == WorkflowEntityType.ticket
    assert clock.due_at - clock.started_at == timedelta(minutes=24 * 60)


# ── real-time breach escalation (Part B) ─────────────────────────────────────


def test_breach_escalates_to_roles_and_emits_event(db_session, monkeypatch):
    events: list[tuple] = []
    monkeypatch.setattr(tickets_service, "emit_event", lambda db, et, payload, **kw: events.append((et, payload)))

    mgr = _person(db_session, "mgr@example.com")
    policy = _ticket_policy(db_session)
    ticket = _ticket(db_session, ticket_manager_person_id=mgr.id, status=TicketStatus.open)
    clock = _breached_clock(db_session, entity_id=ticket.id, policy=policy)

    tickets_service.notify_ticket_sla_breach(db_session, clock)
    db_session.commit()
    db_session.refresh(ticket)

    assert ticket.metadata_["sla_breach_notified"] is True
    notes = db_session.query(Notification).filter(Notification.recipient == "mgr@example.com").all()
    assert any(n.channel == NotificationChannel.push for n in notes)
    assert any(n.channel == NotificationChannel.email for n in notes)
    assert any(et == EventType.ticket_escalated and p.get("reason") == "sla_breach" for et, p in events)


def test_breach_escalation_is_idempotent(db_session, monkeypatch):
    monkeypatch.setattr(tickets_service, "emit_event", lambda *a, **k: None)
    mgr = _person(db_session, "mgr2@example.com")
    policy = _ticket_policy(db_session)
    ticket = _ticket(db_session, ticket_manager_person_id=mgr.id, status=TicketStatus.open)
    clock = _breached_clock(db_session, entity_id=ticket.id, policy=policy)

    tickets_service.notify_ticket_sla_breach(db_session, clock)
    db_session.commit()
    before = db_session.query(Notification).filter(Notification.recipient == "mgr2@example.com").count()

    tickets_service.notify_ticket_sla_breach(db_session, clock)
    db_session.commit()
    after = db_session.query(Notification).filter(Notification.recipient == "mgr2@example.com").count()
    assert after == before  # no duplicate escalation on re-run


def test_breach_event_fires_even_without_role_recipients(db_session, monkeypatch):
    events: list[tuple] = []
    monkeypatch.setattr(tickets_service, "emit_event", lambda db, et, payload, **kw: events.append((et, payload)))
    policy = _ticket_policy(db_session)
    ticket = _ticket(db_session, status=TicketStatus.open)  # no roles assigned
    clock = _breached_clock(db_session, entity_id=ticket.id, policy=policy)

    tickets_service.notify_ticket_sla_breach(db_session, clock)
    assert any(et == EventType.ticket_escalated for et, _ in events)


def test_rebreach_after_reopen_escalates_again(db_session, monkeypatch):
    """Reopening a breached clock clears the breach flag so a later breach
    escalates again (regression for the sticky sla_breach_notified flag)."""
    events: list[int] = []
    monkeypatch.setattr(tickets_service, "emit_event", lambda *a, **k: events.append(1))

    mgr = _person(db_session, "mgr3@example.com")
    policy = _ticket_policy(db_session)
    ticket = _ticket(db_session, ticket_manager_person_id=mgr.id, status=TicketStatus.open)
    clock = _breached_clock(db_session, entity_id=ticket.id, policy=policy)

    tickets_service.notify_ticket_sla_breach(db_session, clock)
    db_session.commit()
    db_session.refresh(ticket)
    assert ticket.metadata_["sla_breach_notified"] is True
    assert len(events) == 1

    # The reopen path clears the breach flags (this is the fix under test).
    tickets_service._clear_ticket_sla_breach_flags(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    assert "sla_breach_notified" not in (ticket.metadata_ or {})

    # A subsequent breach now escalates again instead of being suppressed.
    tickets_service.notify_ticket_sla_breach(db_session, clock)
    db_session.commit()
    assert len(events) == 2


def test_non_ticket_clock_is_ignored(db_session, monkeypatch):
    called: list[int] = []
    monkeypatch.setattr(tickets_service, "emit_event", lambda *a, **k: called.append(1))
    policy = SlaPolicy(name="PT", entity_type=WorkflowEntityType.project_task, is_active=True)
    db_session.add(policy)
    db_session.commit()
    clock = _breached_clock(
        db_session, entity_id=uuid.uuid4(), policy=policy, entity_type=WorkflowEntityType.project_task
    )
    tickets_service.notify_ticket_sla_breach(db_session, clock)
    assert called == []  # not a ticket clock → no escalation
