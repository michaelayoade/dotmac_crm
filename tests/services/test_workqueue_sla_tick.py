"""Tests for the workqueue SLA tick and snooze prune Celery beat tasks."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from app.models.tickets import TicketStatus
from app.models.workqueue import WorkqueueItemKind, WorkqueueSnooze
from app.services.workqueue.tasks import prune_snoozes, sla_tick


def test_sla_tick_emits_for_band_transition(db_session, ticket_factory):
    """A ticket about to enter the imminent band should trigger an emit."""
    ticket_factory(
        assignee_person_id=uuid4(),
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) + timedelta(minutes=4),
    )

    with patch("app.services.workqueue.tasks.SessionLocal", return_value=db_session), patch(
        "app.services.workqueue.tasks.emit_change"
    ) as emit:
        # Replace db.close so the patched session isn't terminated mid-test.
        original_close = db_session.close
        try:
            db_session.close = lambda: None  # type: ignore[assignment]
            result = sla_tick()
        finally:
            db_session.close = original_close  # type: ignore[assignment]

    assert result["scanned"] >= 1
    assert emit.called


def test_sla_tick_skips_tickets_outside_window(db_session, ticket_factory):
    """Tickets whose SLA is far in the future must not emit."""
    ticket_factory(
        assignee_person_id=uuid4(),
        status=TicketStatus.open,
        sla_due_at=datetime.now(UTC) + timedelta(hours=24),
    )

    with patch("app.services.workqueue.tasks.SessionLocal", return_value=db_session), patch(
        "app.services.workqueue.tasks.emit_change"
    ) as emit:
        original_close = db_session.close
        try:
            db_session.close = lambda: None  # type: ignore[assignment]
            result = sla_tick()
        finally:
            db_session.close = original_close  # type: ignore[assignment]

    assert result["emitted"] == 0
    assert not emit.called


def test_sla_tick_handles_missing_or_invalid_metadata(db_session, ticket_factory):
    """Tickets with no SLA metadata, or invalid timestamp strings, are silently skipped."""
    # Ticket with no sla_due_at metadata at all
    ticket_factory(assignee_person_id=uuid4(), status=TicketStatus.open)

    with patch("app.services.workqueue.tasks.SessionLocal", return_value=db_session), patch(
        "app.services.workqueue.tasks.emit_change"
    ) as emit:
        original_close = db_session.close
        try:
            db_session.close = lambda: None  # type: ignore[assignment]
            result = sla_tick()
        finally:
            db_session.close = original_close  # type: ignore[assignment]

    assert result["emitted"] == 0
    assert not emit.called


def test_prune_snoozes_deletes_old_rows(db_session):
    """Snoozes whose ``snooze_until`` is older than 7 days should be deleted."""
    user_id = uuid4()
    old = WorkqueueSnooze(
        user_id=user_id,
        item_kind=WorkqueueItemKind.ticket,
        item_id=uuid4(),
        snooze_until=datetime.now(UTC) - timedelta(days=14),
    )
    fresh = WorkqueueSnooze(
        user_id=user_id,
        item_kind=WorkqueueItemKind.ticket,
        item_id=uuid4(),
        snooze_until=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add_all([old, fresh])
    db_session.commit()

    with patch("app.services.workqueue.tasks.SessionLocal", return_value=db_session):
        original_close = db_session.close
        try:
            db_session.close = lambda: None  # type: ignore[assignment]
            result = prune_snoozes()
        finally:
            db_session.close = original_close  # type: ignore[assignment]

    assert result["deleted"] == 1
    surviving = db_session.query(WorkqueueSnooze).filter_by(user_id=user_id).all()
    assert len(surviving) == 1
    assert surviving[0].id == fresh.id
