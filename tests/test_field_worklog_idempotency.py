"""Offline worklog uploads dedupe on client_ref instead of duplicating.

Regression for retried offline submissions: the client_ref column makes a
replay return the original worklog even when the recomputed start_at drifts.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.timecost import WorkLog
from app.schemas.workforce import WorkOrderUpdate
from app.services.field.worklogs import field_worklogs
from app.services.workforce import work_orders


@pytest.fixture()
def assigned_job(db_session, work_order, person):
    return work_orders.update(db_session, str(work_order.id), WorkOrderUpdate(assigned_to_person_id=person.id))


def _entry(client_ref=None, *, start=None):
    start = start or (datetime.now(UTC) - timedelta(hours=1))
    entry = {"start_at": start, "end_at": start + timedelta(minutes=30)}
    if client_ref:
        entry["client_ref"] = client_ref
    return entry


def test_same_client_ref_dedupes_even_when_start_drifts(db_session, assigned_job, person):
    ref = str(uuid.uuid4())
    first = field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [_entry(ref)])
    assert first[0]["duplicate"] is False

    # Retry with the same client_ref but a drifted start_at: dedup must key on
    # client_ref, not on (person, work_order, start_at).
    second = field_worklogs.submit(
        db_session,
        str(person.id),
        str(assigned_job.id),
        [_entry(ref, start=datetime.now(UTC))],
    )
    assert second[0]["duplicate"] is True

    rows = db_session.query(WorkLog).filter(WorkLog.client_ref == uuid.UUID(ref)).all()
    assert len(rows) == 1


def test_distinct_client_refs_create_distinct_logs(db_session, assigned_job, person):
    base = datetime.now(UTC) - timedelta(hours=4)
    r1 = field_worklogs.submit(
        db_session, str(person.id), str(assigned_job.id), [_entry(str(uuid.uuid4()), start=base)]
    )
    r2 = field_worklogs.submit(
        db_session, str(person.id), str(assigned_job.id), [_entry(str(uuid.uuid4()), start=base + timedelta(hours=1))]
    )
    assert r1[0]["duplicate"] is False
    assert r2[0]["duplicate"] is False


def test_legacy_entry_without_client_ref_still_dedupes(db_session, assigned_job, person):
    start = datetime.now(UTC) - timedelta(hours=2)
    field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [_entry(start=start)])
    second = field_worklogs.submit(db_session, str(person.id), str(assigned_job.id), [_entry(start=start)])
    assert second[0]["duplicate"] is True
