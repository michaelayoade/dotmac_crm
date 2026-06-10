"""Tests for field note creation with attachment linking."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.person import Person
from app.schemas.workforce import WorkOrderUpdate
from app.services.field import attachments as attachments_module
from app.services.field.attachments import field_attachments
from app.services.field.notes import field_notes
from app.services.workforce import work_orders


class _FakeStorage:
    def __init__(self):
        self.objects = {}

    def put(self, key, data, content_type=""):
        self.objects[key] = data
        return key

    def get(self, key):
        return self.objects[key]

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture()
def fake_storage(monkeypatch):
    fake = _FakeStorage()
    monkeypatch.setattr(attachments_module, "storage", fake)
    return fake


@pytest.fixture()
def assigned_job(db_session, work_order, person):
    return work_orders.update(db_session, str(work_order.id), WorkOrderUpdate(assigned_to_person_id=person.id))


def _upload(db, job, person, **overrides):
    payload = {
        "kind": "photo",
        "file_name": "evidence.jpg",
        "mime_type": "image/jpeg",
        "content": b"jpeg",
        "work_order_id": str(job.id),
        "uploaded_by_person_id": str(person.id),
    }
    payload.update(overrides)
    return field_attachments.create(db, **payload)


def test_create_note_with_linked_attachments(db_session, assigned_job, person, fake_storage):
    attachment = _upload(db_session, assigned_job, person)
    note = field_notes.create(
        db_session,
        str(person.id),
        str(assigned_job.id),
        body="Splice complete, photo attached",
        attachment_ids=[str(attachment.id)],
    )
    assert note.author_person_id == person.id
    db_session.refresh(attachment)
    assert attachment.note_id == note.id


def test_foreign_attachment_rejected(db_session, assigned_job, person, fake_storage):
    other = Person(first_name="O", last_name="T", email=f"o-{uuid.uuid4().hex}@example.com")
    db_session.add(other)
    db_session.commit()
    foreign = _upload(db_session, assigned_job, other, uploaded_by_person_id=str(other.id))

    with pytest.raises(HTTPException) as exc:
        field_notes.create(
            db_session,
            str(person.id),
            str(assigned_job.id),
            body="trying to claim someone else's photo",
            attachment_ids=[str(foreign.id)],
        )
    assert exc.value.status_code == 403


def test_attachment_from_other_job_rejected(db_session, assigned_job, person, fake_storage, project, ticket):
    from app.schemas.workforce import WorkOrderCreate

    other_job = work_orders.create(
        db_session,
        WorkOrderCreate(title="Other job", project_id=project.id, ticket_id=ticket.id, assigned_to_person_id=person.id),
    )
    attachment = _upload(db_session, other_job, person, work_order_id=str(other_job.id))

    with pytest.raises(HTTPException) as exc:
        field_notes.create(
            db_session,
            str(person.id),
            str(assigned_job.id),
            body="wrong job",
            attachment_ids=[str(attachment.id)],
        )
    assert exc.value.status_code == 422


def test_unassigned_caller_404(db_session, assigned_job):
    stranger = Person(first_name="S", last_name="T", email=f"s-{uuid.uuid4().hex}@example.com")
    db_session.add(stranger)
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        field_notes.create(db_session, str(stranger.id), str(assigned_job.id), body="hi")
    assert exc.value.status_code == 404


def test_blank_body_rejected(db_session, assigned_job, person):
    with pytest.raises(HTTPException) as exc:
        field_notes.create(db_session, str(person.id), str(assigned_job.id), body="   ")
    assert exc.value.status_code == 422
