"""Tests for the field attachment service and secured file API."""

import uuid

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api.field import router as field_router
from app.models.field import FieldAttachmentKind
from app.models.person import Person
from app.models.vendor import InstallationProject, InstallationProjectStatus, Vendor, VendorUser
from app.schemas.workforce import WorkOrderUpdate
from app.services.field import attachments as attachments_module
from app.services.field import field_attachments
from app.services.workforce import work_orders


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


@pytest.fixture()
def assigned_job(db_session, work_order, person):
    """work_order assigned to `person` — the caller in these tests."""
    return work_orders.update(db_session, str(work_order.id), WorkOrderUpdate(assigned_to_person_id=person.id))


@pytest.fixture()
def stranger(db_session):
    other = Person(first_name="Stray", last_name="Tech", email=f"x-{uuid.uuid4().hex}@example.com")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    return other


def _create(db_session, job, person, fake_storage, **overrides):
    payload = {
        "kind": "photo",
        "file_name": "install.jpg",
        "mime_type": "image/jpeg",
        "content": b"fake-jpeg-bytes",
        "work_order_id": str(job.id),
        "uploaded_by_person_id": str(person.id),
    }
    payload.update(overrides)
    return field_attachments.create(db_session, **payload)


def test_attachment_tags_network_asset(db_session, assigned_job, person, fake_storage):
    from app.models.network import FiberSpliceClosure

    closure = FiberSpliceClosure(name="Closure Photo")
    db_session.add(closure)
    db_session.commit()

    attachment = _create(
        db_session, assigned_job, person, fake_storage, asset_type="splice_closure", asset_id=str(closure.id)
    )

    assert attachment.asset_type == "splice_closure"
    assert attachment.asset_id == closure.id
    # Filterable by the asset it depicts.
    found = field_attachments.list(
        db_session,
        caller_person_id=str(person.id),
        work_order_id=str(assigned_job.id),
        asset_type="splice_closure",
        asset_id=str(closure.id),
    )
    assert [a.id for a in found] == [attachment.id]


def test_attachment_rejects_unknown_asset_type(db_session, assigned_job, person, fake_storage):
    with pytest.raises(HTTPException) as exc:
        _create(db_session, assigned_job, person, fake_storage, asset_type="nonsense", asset_id=str(uuid.uuid4()))
    assert exc.value.status_code == 400


def test_attachment_rejects_missing_asset(db_session, assigned_job, person, fake_storage):
    with pytest.raises(HTTPException) as exc:
        _create(db_session, assigned_job, person, fake_storage, asset_type="splice_closure", asset_id=str(uuid.uuid4()))
    assert exc.value.status_code == 404


def test_attachment_requires_both_asset_fields(db_session, assigned_job, person, fake_storage):
    with pytest.raises(HTTPException) as exc:
        _create(db_session, assigned_job, person, fake_storage, asset_type="splice_closure")
    assert exc.value.status_code == 422


def test_upload_and_download_roundtrip(db_session, assigned_job, fake_storage, person):
    attachment = _create(
        db_session,
        assigned_job,
        person,
        fake_storage,
        latitude=6.5244,
        longitude=3.3792,
        captured_at="2026-06-10T09:30:00+00:00",
    )
    assert attachment.kind == FieldAttachmentKind.photo
    assert attachment.work_order_id == assigned_job.id
    assert attachment.uploaded_by_person_id == person.id
    assert attachment.storage_key.startswith("field-attachments/")

    fetched, content = field_attachments.get_content(db_session, str(attachment.id), caller_person_id=str(person.id))
    assert content == b"fake-jpeg-bytes"
    assert fetched.id == attachment.id


def test_client_ref_makes_upload_idempotent(db_session, assigned_job, person, fake_storage):
    client_ref = str(uuid.uuid4())
    first = _create(db_session, assigned_job, person, fake_storage, client_ref=client_ref)
    replay = _create(db_session, assigned_job, person, fake_storage, client_ref=client_ref, content=b"different")
    assert replay.id == first.id
    assert len(fake_storage.objects) == 1


def test_vendor_project_upload_is_attributed_to_vendor_user(db_session, project, person, fake_storage):
    vendor = Vendor(name="FiberWorks Ltd", is_active=True)
    db_session.add(vendor)
    db_session.flush()
    vendor_user = VendorUser(vendor_id=vendor.id, person_id=person.id, role="crew_lead", is_active=True)
    installation_project = InstallationProject(
        project_id=project.id,
        assigned_vendor_id=vendor.id,
        status=InstallationProjectStatus.in_progress,
    )
    db_session.add_all([vendor_user, installation_project])
    db_session.commit()

    attachment = field_attachments.create(
        db_session,
        kind="photo",
        file_name="as-built.jpg",
        mime_type="image/jpeg",
        content=b"vendor-evidence",
        installation_project_id=str(installation_project.id),
        uploaded_by_person_id=str(person.id),
    )

    assert attachment.uploaded_by_person_id is None
    assert attachment.uploaded_by_vendor_user_id == vendor_user.id
    fetched, content = field_attachments.get_content(
        db_session,
        str(attachment.id),
        caller_person_id=str(person.id),
    )
    assert fetched.id == attachment.id
    assert content == b"vendor-evidence"


def test_oversize_upload_rejected_before_write(db_session, assigned_job, person, fake_storage):
    with pytest.raises(HTTPException) as exc:
        _create(db_session, assigned_job, person, fake_storage, content=b"x" * (5 * 1024 * 1024 + 1))
    assert exc.value.status_code == 413
    assert fake_storage.objects == {}


def test_disallowed_mime_rejected(db_session, assigned_job, person, fake_storage):
    with pytest.raises(HTTPException) as exc:
        _create(
            db_session, assigned_job, person, fake_storage, mime_type="application/x-msdownload", file_name="evil.exe"
        )
    assert exc.value.status_code == 415
    assert fake_storage.objects == {}


def test_empty_file_rejected(db_session, assigned_job, person, fake_storage):
    with pytest.raises(HTTPException) as exc:
        _create(db_session, assigned_job, person, fake_storage, content=b"")
    assert exc.value.status_code == 422


def test_attachment_requires_a_parent(db_session, person, fake_storage):
    with pytest.raises(HTTPException) as exc:
        field_attachments.create(
            db_session,
            kind="photo",
            file_name="orphan.jpg",
            mime_type="image/jpeg",
            content=b"data",
            uploaded_by_person_id=str(person.id),
        )
    assert exc.value.status_code == 422


def test_unknown_work_order_404(db_session, person, fake_storage):
    with pytest.raises(HTTPException) as exc:
        field_attachments.create(
            db_session,
            kind="photo",
            file_name="a.jpg",
            mime_type="image/jpeg",
            content=b"data",
            work_order_id=str(uuid.uuid4()),
            uploaded_by_person_id=str(person.id),
        )
    assert exc.value.status_code == 404


def test_upload_to_unassigned_job_rejected(db_session, assigned_job, stranger, fake_storage):
    with pytest.raises(HTTPException) as exc:
        _create(db_session, assigned_job, stranger, fake_storage)
    assert exc.value.status_code == 404
    assert fake_storage.objects == {}


def test_signature_kind_with_signer_name(db_session, assigned_job, person, fake_storage):
    attachment = _create(
        db_session,
        assigned_job,
        person,
        fake_storage,
        kind="signature",
        file_name="signoff.png",
        mime_type="image/png",
        signer_name="Adaeze Okafor",
    )
    assert attachment.kind == FieldAttachmentKind.signature
    assert attachment.signer_name == "Adaeze Okafor"


def test_invalid_kind_rejected(db_session, assigned_job, person, fake_storage):
    with pytest.raises(HTTPException):
        _create(db_session, assigned_job, person, fake_storage, kind="video")


def test_list_filters_by_work_order_and_kind(db_session, assigned_job, person, fake_storage):
    _create(db_session, assigned_job, person, fake_storage)
    _create(db_session, assigned_job, person, fake_storage, kind="signature", file_name="s.png", mime_type="image/png")

    photos = field_attachments.list(
        db_session, caller_person_id=str(person.id), work_order_id=str(assigned_job.id), kind="photo"
    )
    assert len(photos) == 1
    everything = field_attachments.list(db_session, caller_person_id=str(person.id), work_order_id=str(assigned_job.id))
    assert len(everything) == 2


def test_soft_delete_hides_attachment(db_session, assigned_job, person, fake_storage):
    attachment = _create(db_session, assigned_job, person, fake_storage)
    field_attachments.delete(db_session, str(attachment.id), caller_person_id=str(person.id))
    with pytest.raises(HTTPException) as exc:
        field_attachments.get(db_session, str(attachment.id), caller_person_id=str(person.id))
    assert exc.value.status_code == 404
    assert field_attachments.list(db_session, caller_person_id=str(person.id), work_order_id=str(assigned_job.id)) == []


class TestAttachmentScopingIDOR:
    """A caller not on the attachment's job cannot read/download/delete/list it."""

    def test_get_foreign_attachment_404(self, db_session, assigned_job, person, stranger, fake_storage):
        attachment = _create(db_session, assigned_job, person, fake_storage)
        with pytest.raises(HTTPException) as exc:
            field_attachments.get(db_session, str(attachment.id), caller_person_id=str(stranger.id))
        assert exc.value.status_code == 404

    def test_download_foreign_attachment_404(self, db_session, assigned_job, person, stranger, fake_storage):
        attachment = _create(db_session, assigned_job, person, fake_storage)
        with pytest.raises(HTTPException) as exc:
            field_attachments.get_content(db_session, str(attachment.id), caller_person_id=str(stranger.id))
        assert exc.value.status_code == 404

    def test_delete_foreign_attachment_404(self, db_session, assigned_job, person, stranger, fake_storage):
        attachment = _create(db_session, assigned_job, person, fake_storage)
        with pytest.raises(HTTPException) as exc:
            field_attachments.delete(db_session, str(attachment.id), caller_person_id=str(stranger.id))
        assert exc.value.status_code == 404
        db_session.refresh(attachment)
        assert attachment.is_active is True

    def test_list_foreign_job_404(self, db_session, assigned_job, person, stranger, fake_storage):
        _create(db_session, assigned_job, person, fake_storage)
        with pytest.raises(HTTPException) as exc:
            field_attachments.list(db_session, caller_person_id=str(stranger.id), work_order_id=str(assigned_job.id))
        assert exc.value.status_code == 404

    def test_list_without_scope_rejected(self, db_session, person, fake_storage):
        with pytest.raises(HTTPException) as exc:
            field_attachments.list(db_session, caller_person_id=str(person.id))
        assert exc.value.status_code == 404


def test_attachment_routes_require_auth():
    """Every attachment route resolves the caller (no anonymous IDOR surface)."""
    from app.services.auth_dependencies import require_user_auth

    found = {}
    for route in field_router.routes:
        if isinstance(route, APIRoute) and "/attachments" in route.path:
            has_auth = any(dep.call is require_user_auth for dep in route.dependant.dependencies)
            found[(tuple(sorted(route.methods)), route.path)] = has_auth
    assert found, "no attachment routes found"
    for key, has_auth in found.items():
        assert has_auth, f"{key} missing require_user_auth"


def test_field_routes_are_not_under_static():
    """Field attachment content must be served by the API, never /static."""
    paths = [route.path for route in field_router.routes if isinstance(route, APIRoute)]
    assert "/field/attachments/{attachment_id}/content" in paths
    assert all(not path.startswith("/static") for path in paths)
