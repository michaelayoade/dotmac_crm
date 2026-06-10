"""Tests for the merged field schedule timeline."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.dispatch import AvailabilityBlock, Shift, TechnicianProfile
from app.schemas.workforce import WorkOrderUpdate
from app.services.field.schedule import field_schedule
from app.services.workforce import work_orders


@pytest.fixture()
def technician(db_session, person):
    profile = TechnicianProfile(person_id=person.id, title="Fiber Tech", region="Lagos")
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return profile


def test_timeline_merges_and_sorts(db_session, person, technician, work_order):
    now = datetime.now(UTC)
    work_orders.update(
        db_session,
        str(work_order.id),
        WorkOrderUpdate(assigned_to_person_id=person.id, scheduled_start=now + timedelta(hours=4)),
    )
    db_session.add(
        Shift(technician_id=technician.id, start_at=now + timedelta(hours=1), end_at=now + timedelta(hours=9))
    )
    db_session.add(
        AvailabilityBlock(
            technician_id=technician.id,
            start_at=now + timedelta(hours=6),
            end_at=now + timedelta(hours=7),
            reason="Training",
        )
    )
    db_session.commit()

    timeline = field_schedule.timeline(db_session, str(person.id))
    assert [e["type"] for e in timeline] == ["shift", "job", "availability"]
    assert timeline[0]["title"] in ("Shift", "regular")
    assert timeline[1]["reference_id"] == work_order.id
    assert timeline[2]["title"] == "Training"


def test_window_clamped_to_31_days(db_session, person, technician):
    now = datetime.now(UTC)
    db_session.add(
        Shift(
            technician_id=technician.id,
            start_at=now + timedelta(days=40),
            end_at=now + timedelta(days=40, hours=8),
        )
    )
    db_session.commit()

    timeline = field_schedule.timeline(db_session, str(person.id), date_from=now, date_to=now + timedelta(days=90))
    assert timeline == []  # the day-40 shift falls outside the clamped window


def test_invalid_window_rejected(db_session, person):
    now = datetime.now(UTC)
    with pytest.raises(HTTPException) as exc:
        field_schedule.timeline(db_session, str(person.id), date_from=now, date_to=now - timedelta(days=1))
    assert exc.value.status_code == 422


def test_caller_without_technician_profile_gets_jobs_only(db_session, person, work_order):
    now = datetime.now(UTC)
    work_orders.update(
        db_session,
        str(work_order.id),
        WorkOrderUpdate(assigned_to_person_id=person.id, scheduled_start=now + timedelta(hours=2)),
    )
    timeline = field_schedule.timeline(db_session, str(person.id))
    assert [e["type"] for e in timeline] == ["job"]


def test_other_technicians_jobs_not_visible(db_session, person, technician, work_order):
    timeline = field_schedule.timeline(db_session, str(person.id))
    assert timeline == []  # work_order is unassigned
