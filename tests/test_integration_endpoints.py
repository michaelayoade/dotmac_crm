"""External-integration surface: work-order webhooks + live ticket SLA status."""

from datetime import UTC, datetime, timedelta

from app.models.tickets import Ticket
from app.models.webhook import WebhookEventType
from app.models.workflow import SlaClock, SlaClockStatus, SlaPolicy, WorkflowEntityType
from app.services import sla_assignment
from app.services.events.handlers.webhook import EVENT_TYPE_TO_WEBHOOK
from app.services.events.types import EventType

# ── work-order lifecycle webhooks ────────────────────────────────────────────


def test_work_order_events_are_webhook_deliverable():
    expected = {
        EventType.work_order_dispatched: WebhookEventType.work_order_dispatched,
        EventType.work_order_completed: WebhookEventType.work_order_completed,
        EventType.work_order_canceled: WebhookEventType.work_order_canceled,
    }
    for event_type, webhook_type in expected.items():
        assert EVENT_TYPE_TO_WEBHOOK.get(event_type) == webhook_type


# ── live ticket SLA status ───────────────────────────────────────────────────


def _ticket(db):
    t = Ticket(title="Connectivity issue")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_sla_status_none_without_clock(db_session):
    assert sla_assignment.ticket_sla_status(db_session, _ticket(db_session).id) is None


def test_sla_status_running_reports_time_remaining(db_session):
    ticket = _ticket(db_session)
    policy = SlaPolicy(name="P", entity_type=WorkflowEntityType.ticket, is_active=True)
    db_session.add(policy)
    db_session.commit()
    now = datetime.now(UTC)
    db_session.add(
        SlaClock(
            policy_id=policy.id,
            entity_type=WorkflowEntityType.ticket,
            entity_id=ticket.id,
            status=SlaClockStatus.running,
            started_at=now,
            due_at=now + timedelta(hours=2),
        )
    )
    db_session.commit()
    status = sla_assignment.ticket_sla_status(db_session, ticket.id)
    assert status is not None
    assert status["breached"] is False
    assert status["minutes_remaining"] > 0


def test_sla_status_reports_breach(db_session):
    ticket = _ticket(db_session)
    policy = SlaPolicy(name="P", entity_type=WorkflowEntityType.ticket, is_active=True)
    db_session.add(policy)
    db_session.commit()
    now = datetime.now(UTC)
    db_session.add(
        SlaClock(
            policy_id=policy.id,
            entity_type=WorkflowEntityType.ticket,
            entity_id=ticket.id,
            status=SlaClockStatus.breached,
            started_at=now - timedelta(hours=2),
            due_at=now - timedelta(hours=1),
            breached_at=now - timedelta(hours=1),
        )
    )
    db_session.commit()
    status = sla_assignment.ticket_sla_status(db_session, ticket.id)
    assert status is not None
    assert status["breached"] is True
    assert status["minutes_remaining"] < 0  # past due
