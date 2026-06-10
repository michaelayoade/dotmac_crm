"""Tests for the field attachment service and secured file API."""

import uuid

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api.field import router as field_router
from app.models.field import FieldAttachmentKind
from app.services.field import attachments as attachments_module
from app.services.field import field_attachments


class _FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        self.objects[key] = data
        return f"/fake/{key}"

    def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@pytest.fixture()
def fake_storage(monkeypatch):
    fake = _FakeStorage()
    monkeypatch.setattr(attachments_module, "storage", fake)
    return fake


def _create(db_session, work_order, fake_storage, **overrides):
    payload = {
        "kind": "photo",
        "file_name": "install.jpg",
        "mime_type": "image/jpeg",
        "content": b"fake-jpeg-bytes",
        "work_order_id": str(work_order.id),
    }
    payload.update(overrides)
    return field_attachments.create(db_session, **payload)


def test_upload_and_download_roundtrip(db_session, work_order, fake_storage, person):
    attachment = _create(
        db_session,
        work_order,
        fake_storage,
        latitude=6.5244,
        longitude=3.3792,
        captured_at="2026-06-10T09:30:00+00:00",
        uploaded_by_person_id=str(person.id),
    )
    assert attachment.kind == FieldAttachmentKind.photo
    assert attachment.work_order_id == work_order.id
    assert attachment.latitude == 6.5244
    assert attachment.uploaded_by_person_id == person.id
    assert attachment.storage_key.startswith("field-attachments/")

    fetched, content = field_attachments.get_content(db_session, str(attachment.id))
    assert content == b"fake-jpeg-bytes"
    assert fetched.id == attachment.id


def test_client_ref_makes_upload_idempotent(db_session, work_order, fake_storage):
    client_ref = str(uuid.uuid4())
    first = _create(db_session, work_order, fake_storage, client_ref=client_ref)
    replay = _create(db_session, work_order, fake_storage, client_ref=client_ref, content=b"different")
    assert replay.id == first.id
    # Only one object stored — the replay never re-wrote content.
    assert len(fake_storage.objects) == 1


def test_oversize_upload_rejected_before_write(db_session, work_order, fake_storage):
    with pytest.raises(HTTPException) as exc:
        _create(db_session, work_order, fake_storage, content=b"x" * (5 * 1024 * 1024 + 1))
    assert exc.value.status_code == 413
    assert fake_storage.objects == {}


def test_disallowed_mime_rejected(db_session, work_order, fake_storage):
    with pytest.raises(HTTPException) as exc:
        _create(db_session, work_order, fake_storage, mime_type="application/x-msdownload", file_name="evil.exe")
    assert exc.value.status_code == 415
    assert fake_storage.objects == {}


def test_empty_file_rejected(db_session, work_order, fake_storage):
    with pytest.raises(HTTPException) as exc:
        _create(db_session, work_order, fake_storage, content=b"")
    assert exc.value.status_code == 422


def test_attachment_requires_a_parent(db_session, fake_storage):
    with pytest.raises(HTTPException) as exc:
        field_attachments.create(
            db_session,
            kind="photo",
            file_name="orphan.jpg",
            mime_type="image/jpeg",
            content=b"data",
        )
    assert exc.value.status_code == 422


def test_unknown_work_order_404(db_session, fake_storage):
    with pytest.raises(HTTPException) as exc:
        field_attachments.create(
            db_session,
            kind="photo",
            file_name="a.jpg",
            mime_type="image/jpeg",
            content=b"data",
            work_order_id=str(uuid.uuid4()),
        )
    assert exc.value.status_code == 404


def test_signature_kind_with_signer_name(db_session, work_order, fake_storage):
    attachment = _create(
        db_session,
        work_order,
        fake_storage,
        kind="signature",
        file_name="signoff.png",
        mime_type="image/png",
        signer_name="Adaeze Okafor",
    )
    assert attachment.kind == FieldAttachmentKind.signature
    assert attachment.signer_name == "Adaeze Okafor"


def test_invalid_kind_rejected(db_session, work_order, fake_storage):
    with pytest.raises(HTTPException):
        _create(db_session, work_order, fake_storage, kind="video")


def test_list_filters_by_work_order_and_kind(db_session, work_order, fake_storage):
    _create(db_session, work_order, fake_storage)
    _create(db_session, work_order, fake_storage, kind="signature", file_name="s.png", mime_type="image/png")

    photos = field_attachments.list(db_session, work_order_id=str(work_order.id), kind="photo")
    assert len(photos) == 1
    everything = field_attachments.list(db_session, work_order_id=str(work_order.id))
    assert len(everything) == 2


def test_soft_delete_hides_attachment(db_session, work_order, fake_storage):
    attachment = _create(db_session, work_order, fake_storage)
    field_attachments.delete(db_session, str(attachment.id))
    with pytest.raises(HTTPException) as exc:
        field_attachments.get(db_session, str(attachment.id))
    assert exc.value.status_code == 404
    assert field_attachments.list(db_session, work_order_id=str(work_order.id)) == []


def test_field_routes_are_not_under_static():
    """Field attachment content must be served by the API, never /static."""
    paths = [route.path for route in field_router.routes if isinstance(route, APIRoute)]
    assert "/field/attachments/{attachment_id}/content" in paths
    assert all(not path.startswith("/static") for path in paths)
