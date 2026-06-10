"""Tests for field worklog submission and validation."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.person import Person
from app.schemas.workforce import WorkOrderUpdate
from app.services.field.worklogs import field_worklogs, stop_open_worklog
from app.services.workforce import work_orders


@pytest.fixture()
def assigned_job(db_session, work_order, person):
    return work_orders.update(db_session, str(work_order.id), WorkOrderUpdate(assigned_to_person_id=person.id))


def _entry(start_offset_minutes: int, duration_minutes: int | None = 60, notes: str | None = None) -> dict:
    start = datetime.now(UTC) - timedelta(minutes=start_offset_minutes)
    return {
        "start_at": start,
        "end_at": start + timedelta(minutes=duration_minutes) if duration_minutes is not None else None,
        "notes": notes,
    }


def test_submit_closed_entry(db_session, assigned_job, person):
    results = field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [_entry(120)])
    assert len(results) == 1
    log = results[0]["worklog"]
    assert log.minutes == 60
    assert results[0]["duplicate"] is False
    assert results[0]["backdated"] is False


def test_duplicate_retry_is_idempotent(db_session, assigned_job, person):
    entry = _entry(120)
    first = field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [entry])
    replay = field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [entry])
    assert replay[0]["duplicate"] is True
    assert replay[0]["worklog"].id == first[0]["worklog"].id


def test_overlap_rejected(db_session, assigned_job, person):
    field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [_entry(120)])
    with pytest.raises(HTTPException) as exc:
        field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [_entry(90)])
    assert exc.value.status_code == 409


def test_end_before_start_rejected(db_session, assigned_job, person):
    start = datetime.now(UTC)
    with pytest.raises(HTTPException) as exc:
        field_worklogs.submit(
            db_session,
            str(person.id),
            str(assigned_job.id),
            [{"start_at": start, "end_at": start - timedelta(minutes=5)}],
        )
    assert exc.value.status_code == 422


def test_excessive_duration_rejected(db_session, assigned_job, person):
    with pytest.raises(HTTPException) as exc:
        field_worklogs.submit(
            db_session,
            str(person.id),
            str(assigned_job.id),
            [_entry(20 * 60, duration_minutes=17 * 60)],
        )
    assert exc.value.status_code == 422


def test_backdated_entry_accepted_and_flagged(db_session, assigned_job, person):
    results = field_worklogs.submit(
        db_session,
        str(person.id),
        str(assigned_job.id),
        [_entry(10 * 24 * 60)],  # 10 days ago
    )
    assert results[0]["backdated"] is True


def test_open_timer_then_second_timer_rejected(db_session, assigned_job, person):
    field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [_entry(30, duration_minutes=None)])
    with pytest.raises(HTTPException) as exc:
        field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [_entry(5, duration_minutes=None)])
    assert exc.value.status_code == 409


def test_stop_open_worklog(db_session, assigned_job, person):
    field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [_entry(45, duration_minutes=None)])
    stopped = stop_open_worklog(db_session, assigned_job.id, person.id)
    assert stopped is not None
    assert stopped.end_at is not None
    assert stopped.minutes >= 44
    # Idempotent: nothing left open.
    assert stop_open_worklog(db_session, assigned_job.id, person.id) is None


def test_unassigned_caller_404(db_session, assigned_job):
    stranger = Person(first_name="S", last_name="T", email=f"s-{uuid.uuid4().hex}@example.com")
    db_session.add(stranger)
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        field_worklogs.submit(db_session, str(stranger.id), str(assigned_job.id), [_entry(60)])
    assert exc.value.status_code == 404


def test_rates_never_in_results(db_session, assigned_job, person):
    from app.schemas.field import FieldWorkLogRead

    results = field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [_entry(200, 30)])
    serialized = FieldWorkLogRead.model_validate(results[0]["worklog"]).model_dump()
    assert "hourly_rate" not in serialized
    assert "cost" not in str(serialized).lower()
