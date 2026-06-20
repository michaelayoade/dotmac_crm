"""Tests for field equipment (ONT serial) recording."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.network import OntAssignment, OntUnit
from app.models.subscriber import Subscriber
from app.schemas.workforce import WorkOrderUpdate
from app.services.field.equipment import field_equipment
from app.services.workforce import work_orders


@pytest.fixture()
def assigned_job(db_session, work_order, person):
    return work_orders.update(db_session, str(work_order.id), WorkOrderUpdate(assigned_to_person_id=person.id))


@pytest.fixture()
def job_with_subscriber(db_session, assigned_job, person):
    subscriber = Subscriber(person_id=person.id)
    db_session.add(subscriber)
    db_session.flush()
    assigned_job.subscriber_id = subscriber.id
    db_session.commit()
    db_session.refresh(assigned_job)
    return assigned_job, subscriber


def test_record_new_serial(db_session, job_with_subscriber, person):
    job, subscriber = job_with_subscriber
    assignment = field_equipment.record(
        db_session,
        str(person.id),
        str(job.id),
        serial_number="hwtc-1234abcd",
        vendor="Huawei",
        model="HG8245",
    )
    assert assignment.subscriber_id == subscriber.id
    assert assignment.work_order_id == job.id
    assert assignment.person_id == person.id
    assert assignment.active is True
    assert assignment.ont_unit.serial_number == "HWTC-1234ABCD"  # normalized upper


def test_replacement_deactivates_old_assignment(db_session, job_with_subscriber, person):
    job, subscriber = job_with_subscriber
    first = field_equipment.record(db_session, str(person.id), str(job.id), serial_number="OLD-0001")
    second = field_equipment.record(db_session, str(person.id), str(job.id), serial_number="NEW-0002")

    db_session.refresh(first)
    assert first.active is False
    assert second.active is True

    active = (
        db_session.query(OntAssignment)
        .filter(OntAssignment.subscriber_id == subscriber.id)
        .filter(OntAssignment.active.is_(True))
        .all()
    )
    assert [a.id for a in active] == [second.id]


def test_duplicate_serial_reuses_unit(db_session, job_with_subscriber, person):
    job, _ = job_with_subscriber
    field_equipment.record(db_session, str(person.id), str(job.id), serial_number="SAME-001")
    field_equipment.record(db_session, str(person.id), str(job.id), serial_number="same-001")
    assert db_session.query(OntUnit).filter(OntUnit.serial_number == "SAME-001").count() == 1


def test_job_without_subscriber_422(db_session, assigned_job, person):
    with pytest.raises(HTTPException) as exc:
        field_equipment.record(db_session, str(person.id), str(assigned_job.id), serial_number="X-1")
    assert exc.value.status_code == 422


def test_current_for_job(db_session, job_with_subscriber, person):
    job, _ = job_with_subscriber
    assert field_equipment.current_for_job(db_session, str(person.id), str(job.id)) is None
    field_equipment.record(db_session, str(person.id), str(job.id), serial_number="CUR-1")
    current = field_equipment.current_for_job(db_session, str(person.id), str(job.id))
    assert current.ont_unit.serial_number == "CUR-1"


def test_unassigned_caller_404(db_session, job_with_subscriber):
    from app.models.person import Person

    job, _ = job_with_subscriber
    stranger = Person(first_name="S", last_name="T", email=f"s-{uuid.uuid4().hex}@example.com")
    db_session.add(stranger)
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        field_equipment.record(db_session, str(stranger.id), str(job.id), serial_number="X-2")
    assert exc.value.status_code == 404


def test_only_one_active_assignment_after_repeat_records(db_session, job_with_subscriber, person):
    job, subscriber = job_with_subscriber
    # Two records for the same subscriber (different serials) must leave exactly
    # one active assignment — the latter.
    field_equipment.record(db_session, str(person.id), str(job.id), serial_number="A-1")
    field_equipment.record(db_session, str(person.id), str(job.id), serial_number="A-2")

    active = (
        db_session.query(OntAssignment)
        .filter(OntAssignment.subscriber_id == subscriber.id)
        .filter(OntAssignment.active.is_(True))
        .all()
    )
    assert len(active) == 1
    assert active[0].ont_unit.serial_number == "A-2"
