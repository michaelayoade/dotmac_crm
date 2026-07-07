"""Tests for technician-scoped field job views."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.field import FieldJobEvent, WorkOrderEvent
from app.models.inventory import InventoryItem, WorkOrderMaterial
from app.models.person import Person
from app.models.tickets import TicketComment
from app.models.timecost import WorkLog
from app.models.workforce import WorkOrderAssignment
from app.schemas.field import FieldJobDetail, FieldNoteRead, FieldWorkLogRead
from app.schemas.material_request import MaterialRequestCreate, MaterialRequestItemCreate
from app.schemas.workforce import WorkOrderNoteCreate, WorkOrderUpdate
from app.services.field.jobs import field_jobs
from app.services.material_requests import material_requests
from app.services.workforce import work_order_notes, work_orders


@pytest.fixture()
def other_person(db_session):
    other = Person(first_name="Other", last_name="Tech", email=f"other-{uuid.uuid4().hex}@example.com")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    return other


@pytest.fixture()
def assigned_job(db_session, work_order, person):
    return work_orders.update(db_session, str(work_order.id), WorkOrderUpdate(assigned_to_person_id=person.id))


def test_jobs_scoped_to_assigned_technician(db_session, assigned_job, person, other_person):
    mine = field_jobs.list(db_session, str(person.id))
    assert [wo.id for wo in mine] == [assigned_job.id]

    theirs = field_jobs.list(db_session, str(other_person.id))
    assert theirs == []


def test_assignment_member_sees_job(db_session, assigned_job, other_person):
    db_session.add(WorkOrderAssignment(work_order_id=assigned_job.id, person_id=other_person.id, role="helper"))
    db_session.commit()

    visible = field_jobs.list(db_session, str(other_person.id))
    assert [wo.id for wo in visible] == [assigned_job.id]


def test_detail_404_for_unassigned_caller(db_session, assigned_job, other_person):
    with pytest.raises(HTTPException) as exc:
        field_jobs.get_detail(db_session, str(other_person.id), str(assigned_job.id))
    assert exc.value.status_code == 404
    # Same 404 as a missing job: existence must not leak.
    with pytest.raises(HTTPException) as missing:
        field_jobs.get_detail(db_session, str(other_person.id), str(uuid.uuid4()))
    assert missing.value.status_code == exc.value.status_code


def test_detail_bundle_contents(db_session, assigned_job, person, ticket):
    work_order_notes.create(
        db_session,
        WorkOrderNoteCreate(work_order_id=assigned_job.id, body="On site", author_person_id=person.id),
    )
    item = InventoryItem(name="Drop cable", sku="DC-150")
    db_session.add(item)
    db_session.flush()
    db_session.add(WorkOrderMaterial(work_order_id=assigned_job.id, item_id=item.id, quantity=2))
    request = material_requests.create(
        db_session,
        MaterialRequestCreate(
            work_order_id=assigned_job.id,
            requested_by_person_id=person.id,
            items=[MaterialRequestItemCreate(item_id=item.id, quantity=1)],
        ),
    )
    db_session.add(
        WorkLog(
            work_order_id=assigned_job.id,
            person_id=person.id,
            start_at=assigned_job.created_at,
            minutes=30,
            hourly_rate=99,
        )
    )
    db_session.add(
        WorkOrderEvent(
            work_order_id=assigned_job.id,
            event=FieldJobEvent.start,
            actor_person_id=person.id,
            occurred_at=assigned_job.created_at,
            client_event_id=uuid.uuid4(),
        )
    )
    db_session.commit()

    bundle = field_jobs.get_detail(db_session, str(person.id), str(assigned_job.id))
    assert bundle["work_order"].id == assigned_job.id
    assert bundle["ticket_ref"] == (ticket.number or str(ticket.id))
    assert len(bundle["notes"]) == 1
    note_read = FieldNoteRead.from_note(bundle["notes"][0])
    assert note_read.author_name == f"{person.first_name} {person.last_name}"
    assert [m.item.name for m in bundle["materials"]] == ["Drop cable"]
    assert [mr.id for mr in bundle["material_requests"]] == [request.id]
    assert len(bundle["worklogs"]) == 1
    history = bundle["history"]
    assert {item["type"] for item in history} >= {"note", "material_request", "work_event", "worklog"}
    assert history == sorted(history, key=lambda item: item["occurred_at"], reverse=True)
    material_item = next(item for item in history if item["type"] == "material_request")
    assert material_item["metadata"]["material_request_id"] == str(request.id)
    assert material_item["description"] == "submitted · 1 item"
    note_item = next(item for item in history if item["type"] == "note")
    assert note_item["is_internal"] is True


def test_work_order_note_mirrors_to_linked_ticket(db_session, assigned_job, person):
    attachments = [
        {
            "file_name": "before.jpg",
            "file_size": 1024,
            "mime_type": "image/jpeg",
            "url": "/api/v1/field/attachments/example/content",
        }
    ]
    note = work_order_notes.create(
        db_session,
        WorkOrderNoteCreate(
            work_order_id=assigned_job.id,
            body="Customer confirmed access",
            author_person_id=person.id,
            attachments=attachments,
        ),
    )

    comment = db_session.query(TicketComment).filter(TicketComment.ticket_id == assigned_job.ticket_id).one()
    assert comment.author_person_id == person.id
    assert comment.is_internal is True
    assert comment.attachments == attachments
    assert str(assigned_job.id)[:8] in comment.body
    assert note.body in comment.body


def test_external_work_order_note_mirrors_to_external_ticket_comment(db_session, assigned_job, person, monkeypatch):
    customer_updates: list[dict] = []

    def _capture_customer_update(db, *, ticket_id, comment_id, actor_person_id, request=None):
        customer_updates.append(
            {
                "ticket_id": ticket_id,
                "comment_id": comment_id,
                "actor_person_id": actor_person_id,
                "request": request,
            }
        )
        return {"ticket_id": ticket_id, "comment_id": comment_id}

    monkeypatch.setattr(
        "app.services.tickets.tickets.notify_customer_of_public_technician_comment",
        _capture_customer_update,
    )

    note = work_order_notes.create(
        db_session,
        WorkOrderNoteCreate(
            work_order_id=assigned_job.id,
            body="Customer-facing update",
            author_person_id=person.id,
            is_internal=False,
        ),
    )

    comment = db_session.query(TicketComment).filter(TicketComment.ticket_id == assigned_job.ticket_id).one()
    assert note.is_internal is False
    assert comment.is_internal is False
    assert note.body in comment.body
    assert customer_updates == [
        {
            "ticket_id": str(assigned_job.ticket_id),
            "comment_id": str(comment.id),
            "actor_person_id": str(person.id),
            "request": None,
        }
    ]


def test_worklog_schema_never_exposes_rates():
    assert "hourly_rate" not in FieldWorkLogRead.model_fields
    detail_fields = set(FieldJobDetail.model_fields)
    assert "cost" not in str(detail_fields).lower()


def test_me_counts(db_session, assigned_job, person):
    me = field_jobs.me(db_session, str(person.id))
    assert me["person_id"] == person.id
    assert me["open_jobs"] == 0  # job is in draft status, not scheduled/dispatched/in_progress

    work_orders.update(db_session, str(assigned_job.id), WorkOrderUpdate(status="dispatched"))
    me = field_jobs.me(db_session, str(person.id))
    assert me["open_jobs"] == 1


def test_status_filter(db_session, assigned_job, person):
    work_orders.update(db_session, str(assigned_job.id), WorkOrderUpdate(status="dispatched"))
    assert len(field_jobs.list(db_session, str(person.id), status="dispatched")) == 1
    assert field_jobs.list(db_session, str(person.id), status="completed") == []
    with pytest.raises(HTTPException):
        field_jobs.list(db_session, str(person.id), status="bogus")
