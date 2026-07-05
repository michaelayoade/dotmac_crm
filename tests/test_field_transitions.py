"""Tests for the field job transition service."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.audit import AuditEvent
from app.models.field import FieldJobEvent, WorkOrderEvent, WorkOrderMovement
from app.models.person import Person
from app.models.tickets import TicketComment
from app.models.timecost import WorkLog
from app.models.workforce import WorkOrderAssignment, WorkOrderStatus
from app.schemas.workforce import WorkOrderUpdate
from app.services.field.attachments import field_attachments
from app.services.field.transitions import field_transitions
from app.services.workforce import work_orders


@pytest.fixture()
def fake_storage(monkeypatch):
    from app.services.field import attachments as attachments_module

    class _Fake:
        def __init__(self):
            self.objects = {}

        def put(self, key, data, content_type=""):
            self.objects[key] = data
            return key

        def get(self, key):
            return self.objects[key]

        def delete(self, key):
            self.objects.pop(key, None)

    fake = _Fake()
    monkeypatch.setattr(attachments_module, "storage", fake)
    return fake


@pytest.fixture()
def dispatched_job(db_session, work_order, person):
    work_orders.update(
        db_session,
        str(work_order.id),
        WorkOrderUpdate(assigned_to_person_id=person.id, status="dispatched"),
    )
    db_session.refresh(work_order)
    return work_order


def _apply(db, person, job, event, **kwargs):
    kwargs.setdefault("client_event_id", str(uuid.uuid4()))
    return field_transitions.apply(db, str(person.id), str(job.id), event=event, **kwargs)


def _add_evidence(db, job, fake_storage, person, *, signature=True):
    field_attachments.create(
        db,
        kind="photo",
        file_name="done.jpg",
        mime_type="image/jpeg",
        content=b"jpeg",
        work_order_id=str(job.id),
        uploaded_by_person_id=str(person.id),
    )
    if signature:
        field_attachments.create(
            db,
            kind="signature",
            file_name="sig.png",
            mime_type="image/png",
            content=b"png",
            work_order_id=str(job.id),
            signer_name="Customer",
            uploaded_by_person_id=str(person.id),
        )


def test_start_sets_in_progress_and_timestamps(db_session, dispatched_job, person):
    result = _apply(db_session, person, dispatched_job, "start", latitude=6.5, longitude=3.4)
    assert result["replayed"] is False
    db_session.refresh(dispatched_job)
    assert dispatched_job.status == WorkOrderStatus.in_progress
    assert dispatched_job.started_at is not None
    open_log = (
        db_session.query(WorkLog)
        .filter(WorkLog.work_order_id == dispatched_job.id)
        .filter(WorkLog.person_id == person.id)
        .filter(WorkLog.end_at.is_(None))
        .one()
    )
    assert open_log.start_at is not None

    event = db_session.query(WorkOrderEvent).filter_by(work_order_id=dispatched_job.id).one()
    assert event.event == FieldJobEvent.start
    assert event.latitude == 6.5

    audit = db_session.query(AuditEvent).filter(AuditEvent.action == "field:job:start").first()
    assert audit is not None
    assert audit.entity_id == str(dispatched_job.id)

    comment = db_session.query(TicketComment).filter(TicketComment.ticket_id == dispatched_job.ticket_id).one()
    assert comment.is_internal is True
    assert "Field update" in comment.body
    assert "started" in comment.body


def test_replay_is_idempotent(db_session, dispatched_job, person):
    client_event_id = str(uuid.uuid4())
    first = _apply(db_session, person, dispatched_job, "start", client_event_id=client_event_id)
    replay = _apply(db_session, person, dispatched_job, "start", client_event_id=client_event_id)
    assert replay["replayed"] is True
    assert replay["event"].id == first["event"].id
    assert db_session.query(WorkOrderEvent).filter_by(work_order_id=dispatched_job.id).count() == 1
    assert db_session.query(TicketComment).filter_by(ticket_id=dispatched_job.ticket_id).count() == 1


def test_helper_cannot_transition(db_session, dispatched_job, person):
    helper = Person(first_name="Helper", last_name="Tech", email=f"h-{uuid.uuid4().hex}@example.com")
    db_session.add(helper)
    db_session.commit()
    db_session.add(WorkOrderAssignment(work_order_id=dispatched_job.id, person_id=helper.id, role="helper"))
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        _apply(db_session, helper, dispatched_job, "start")
    assert exc.value.status_code == 403


def test_complete_requires_in_progress(db_session, dispatched_job, person):
    with pytest.raises(HTTPException) as exc:
        _apply(db_session, person, dispatched_job, "complete")
    assert exc.value.status_code == 409


def test_completion_gate_blocks_without_evidence(db_session, dispatched_job, person, fake_storage):
    _apply(db_session, person, dispatched_job, "start")
    with pytest.raises(HTTPException) as exc:
        _apply(db_session, person, dispatched_job, "complete")
    assert exc.value.status_code == 422


def test_completion_with_photo_and_signature(db_session, dispatched_job, person, fake_storage):
    _apply(db_session, person, dispatched_job, "start")
    _add_evidence(db_session, dispatched_job, fake_storage, person)

    _apply(db_session, person, dispatched_job, "complete")
    db_session.refresh(dispatched_job)
    assert dispatched_job.status == WorkOrderStatus.completed
    assert dispatched_job.completed_at is not None


def test_completion_with_signature_fallback(db_session, dispatched_job, person, fake_storage):
    _apply(db_session, person, dispatched_job, "start")
    _add_evidence(db_session, dispatched_job, fake_storage, person, signature=False)

    _apply(
        db_session,
        person,
        dispatched_job,
        "complete",
        payload={"signature_unavailable_reason": "customer absent"},
    )
    db_session.refresh(dispatched_job)
    assert dispatched_job.status == WorkOrderStatus.completed


def test_total_active_time_excludes_paused_time(db_session, dispatched_job, person, fake_storage):
    start_at = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    pause_at = start_at + timedelta(minutes=30)
    resume_at = start_at + timedelta(minutes=60)
    complete_at = start_at + timedelta(minutes=90)

    _apply(db_session, person, dispatched_job, "start", occurred_at=start_at)
    _apply(db_session, person, dispatched_job, "pause", occurred_at=pause_at)
    _apply(db_session, person, dispatched_job, "resume", occurred_at=resume_at)
    _add_evidence(db_session, dispatched_job, fake_storage, person)
    _apply(db_session, person, dispatched_job, "complete", occurred_at=complete_at)

    db_session.refresh(dispatched_job)
    assert dispatched_job.status == WorkOrderStatus.completed
    assert dispatched_job.total_active_seconds == 60 * 60


def test_clock_skew_is_flagged_not_rejected(db_session, dispatched_job, person):
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    result = _apply(db_session, person, dispatched_job, "start", occurred_at=old)
    assert result["event"].payload["clock_skew_seconds"] >= 7000


def test_pause_sets_paused_and_stops_open_worklog(db_session, dispatched_job, person):
    start_at = datetime.now(UTC) - timedelta(minutes=45)
    _apply(db_session, person, dispatched_job, "start", occurred_at=start_at)

    _apply(db_session, person, dispatched_job, "pause", note="overnight")
    db_session.refresh(dispatched_job)
    assert dispatched_job.status == WorkOrderStatus.paused
    assert dispatched_job.paused_at is not None
    assert dispatched_job.total_active_seconds is not None
    assert dispatched_job.total_active_seconds >= 44 * 60
    open_logs = (
        db_session.query(WorkLog)
        .filter(WorkLog.work_order_id == dispatched_job.id)
        .filter(WorkLog.end_at.is_(None))
        .count()
    )
    assert open_logs == 0
    closed = db_session.query(WorkLog).filter(WorkLog.work_order_id == dispatched_job.id).one()
    assert closed.minutes >= 44


def test_double_pause_is_idempotent(db_session, dispatched_job, person):
    """A second pause tap (fresh client_event_id) collapses to a no-op."""
    _apply(db_session, person, dispatched_job, "start")
    first = _apply(db_session, person, dispatched_job, "pause", note="lunch")
    assert first["replayed"] is False

    second = _apply(db_session, person, dispatched_job, "pause", note="still lunch")
    assert second["replayed"] is True
    assert second["event"].id == first["event"].id

    pause_events = (
        db_session.query(WorkOrderEvent)
        .filter(WorkOrderEvent.work_order_id == dispatched_job.id)
        .filter(WorkOrderEvent.event == FieldJobEvent.pause)
        .count()
    )
    assert pause_events == 1


def test_resume_after_pause_then_double_resume_is_idempotent(db_session, dispatched_job, person):
    _apply(db_session, person, dispatched_job, "start")
    _apply(db_session, person, dispatched_job, "pause")
    first_resume = _apply(db_session, person, dispatched_job, "resume")
    assert first_resume["replayed"] is False
    db_session.refresh(dispatched_job)
    assert dispatched_job.status == WorkOrderStatus.in_progress
    assert dispatched_job.resumed_at is not None

    # A second resume (already running) is a no-op; a fresh pause is recorded.
    second_resume = _apply(db_session, person, dispatched_job, "resume")
    assert second_resume["replayed"] is True
    again_pause = _apply(db_session, person, dispatched_job, "pause")
    assert again_pause["replayed"] is False

    counts = {
        FieldJobEvent.pause: 2,  # original + the re-pause after resume
        FieldJobEvent.resume: 1,
    }
    for event, expected in counts.items():
        got = (
            db_session.query(WorkOrderEvent)
            .filter(WorkOrderEvent.work_order_id == dispatched_job.id)
            .filter(WorkOrderEvent.event == event)
            .count()
        )
        assert got == expected, f"{event}: expected {expected}, got {got}"


def test_unassigned_caller_gets_404(db_session, dispatched_job):
    stranger = Person(first_name="S", last_name="T", email=f"s-{uuid.uuid4().hex}@example.com")
    db_session.add(stranger)
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        _apply(db_session, stranger, dispatched_job, "start")
    assert exc.value.status_code == 404


class TestCustomerNotifications:
    """en_route and complete fire customer notifications, replay-safe."""

    def test_en_route_sends_on_my_way(self, db_session, dispatched_job, person, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "app.services.eta_notifications.send_eta_notification",
            lambda db, wo_id: calls.append(wo_id) or True,
        )
        _apply(db_session, person, dispatched_job, "en_route")
        assert calls == [str(dispatched_job.id)]

    def test_en_route_to_internal_destination_does_not_notify_customer(
        self, db_session, dispatched_job, person, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            "app.services.eta_notifications.send_eta_notification",
            lambda db, wo_id: calls.append(wo_id) or True,
        )
        _apply(
            db_session,
            person,
            dispatched_job,
            "en_route",
            payload={"destination_type": "cabinet", "destination_label": "FDH-12"},
        )
        assert calls == []

    def test_complete_sends_completion_notification(
        self, db_session, dispatched_job, person, fake_storage, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            "app.services.eta_notifications.send_work_order_completed_notification",
            lambda db, wo_id: calls.append(wo_id) or True,
        )
        _apply(db_session, person, dispatched_job, "start")
        _add_evidence(db_session, dispatched_job, fake_storage, person)
        _apply(db_session, person, dispatched_job, "complete")
        assert calls == [str(dispatched_job.id)]

    def test_replay_does_not_resend(self, db_session, dispatched_job, person, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "app.services.eta_notifications.send_eta_notification",
            lambda db, wo_id: calls.append(wo_id) or True,
        )
        client_event_id = str(uuid.uuid4())
        _apply(db_session, person, dispatched_job, "en_route", client_event_id=client_event_id)
        _apply(db_session, person, dispatched_job, "en_route", client_event_id=client_event_id)
        assert len(calls) == 1

    def test_notification_failure_does_not_break_transition(self, db_session, dispatched_job, person, monkeypatch):
        def _boom(db, wo_id):
            raise RuntimeError("smtp down")

        monkeypatch.setattr("app.services.eta_notifications.send_eta_notification", _boom)
        result = _apply(db_session, person, dispatched_job, "en_route")
        assert result["replayed"] is False

    def test_arrived_to_customer_sends_arrival_notification(self, db_session, dispatched_job, person, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "app.services.eta_notifications.send_technician_arrived_notification",
            lambda db, wo_id: calls.append(wo_id) or True,
        )
        _apply(db_session, person, dispatched_job, "arrived")
        assert calls == [str(dispatched_job.id)]


def test_en_route_creates_movement_session(db_session, dispatched_job, person):
    result = _apply(
        db_session,
        person,
        dispatched_job,
        "en_route",
        latitude=6.5,
        longitude=3.4,
        payload={"destination_type": "cabinet", "destination_label": "FDH-12"},
    )
    assert result["replayed"] is False
    movement = db_session.query(WorkOrderMovement).filter_by(work_order_id=dispatched_job.id).one()
    assert movement.status == "en_route"
    assert movement.destination_type == "cabinet"
    assert movement.destination_label == "FDH-12"
    assert movement.start_latitude == 6.5


def test_arrived_closes_active_movement_session(db_session, dispatched_job, person):
    _apply(
        db_session,
        person,
        dispatched_job,
        "en_route",
        payload={"destination_type": "closure", "destination_label": "Closure A"},
    )
    movement = db_session.query(WorkOrderMovement).filter_by(work_order_id=dispatched_job.id).one()
    _apply(db_session, person, dispatched_job, "arrived", latitude=6.55, longitude=3.45)
    db_session.refresh(movement)
    assert movement.status == "arrived"
    assert movement.arrived_at is not None
    assert movement.arrival_latitude == 6.55


def test_replay_enforces_caller_access(db_session, dispatched_job, person):
    """A replayed client_event_id must not leak a job to a non-assigned caller."""
    import uuid as _uuid

    from app.models.person import Person

    client_event_id = str(_uuid.uuid4())
    _apply(db_session, person, dispatched_job, "start", client_event_id=client_event_id)

    stranger = Person(first_name="S", last_name="T", email=f"s-{_uuid.uuid4().hex}@example.com")
    db_session.add(stranger)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        field_transitions.apply(
            db_session, str(stranger.id), str(dispatched_job.id), event="start", client_event_id=client_event_id
        )
    assert exc.value.status_code == 404


def test_unable_to_complete_cancels_with_reason(db_session, dispatched_job, person):
    result = _apply(db_session, person, dispatched_job, "unable_to_complete", payload={"reason": "customer_absent"})
    assert result["replayed"] is False
    db_session.refresh(dispatched_job)
    assert dispatched_job.status == WorkOrderStatus.canceled

    event = db_session.query(WorkOrderEvent).filter_by(work_order_id=dispatched_job.id).one()
    assert event.event == FieldJobEvent.unable_to_complete
    assert event.payload["reason"] == "customer_absent"

    audit = db_session.query(AuditEvent).filter(AuditEvent.action == "field:job:unable_to_complete").first()
    assert audit is not None


def test_unable_to_complete_requires_reason(db_session, dispatched_job, person):
    with pytest.raises(HTTPException) as exc:
        _apply(db_session, person, dispatched_job, "unable_to_complete")
    assert exc.value.status_code == 422


def test_unable_to_complete_rejects_unknown_reason(db_session, dispatched_job, person):
    with pytest.raises(HTTPException) as exc:
        _apply(db_session, person, dispatched_job, "unable_to_complete", payload={"reason": "bored"})
    assert exc.value.status_code == 422


def test_unable_to_complete_bypasses_completion_gate(db_session, dispatched_job, person):
    # No photo/signature evidence, yet a failed visit can still be recorded.
    _apply(db_session, person, dispatched_job, "start")
    result = _apply(db_session, person, dispatched_job, "unable_to_complete", payload={"reason": "no_access"})
    db_session.refresh(dispatched_job)
    assert dispatched_job.status == WorkOrderStatus.canceled
    assert result["event"].payload["reason"] == "no_access"


def test_unable_to_complete_stops_open_worklog(db_session, dispatched_job, person):
    _apply(db_session, person, dispatched_job, "start")
    _apply(db_session, person, dispatched_job, "unable_to_complete", payload={"reason": "unsafe"})
    open_logs = (
        db_session.query(WorkLog)
        .filter(WorkLog.work_order_id == dispatched_job.id)
        .filter(WorkLog.end_at.is_(None))
        .count()
    )
    assert open_logs == 0


def test_repeated_en_route_notifies_customer_once(db_session, dispatched_job, person, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.eta_notifications.send_eta_notification",
        lambda db, wo_id: calls.append(wo_id) or True,
    )
    # dispatched -> dispatched is allowed; two distinct en_route taps.
    _apply(db_session, person, dispatched_job, "en_route")
    _apply(db_session, person, dispatched_job, "en_route")
    assert calls == [str(dispatched_job.id)]  # only the first notifies
