"""Field attachment service: photos, signatures, and documents from the field.

Content is stored through the storage backend under UUID keys and served only
via the authenticated field API — never via the public /static mount, because
field evidence contains customer PII and legally relevant signatures.
"""

from __future__ import annotations

import io
import logging
import uuid
from datetime import datetime
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.field import FieldAttachment, FieldAttachmentKind
from app.models.vendor import InstallationProject
from app.models.workforce import WorkOrder, WorkOrderNote
from app.services.common import apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin
from app.services.storage import storage

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
}
_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_STORAGE_PREFIX = "field-attachments"


def _safe_extension(file_name: str) -> str:
    suffix = PurePosixPath(file_name or "").suffix.lower()
    if suffix and len(suffix) <= 8 and suffix[1:].isalnum():
        return suffix
    return ""


def _exif_gps_to_degrees(value) -> float | None:
    try:
        d, m, s = value
        return float(d) + float(m) / 60.0 + float(s) / 3600.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _process_image(content: bytes, mime_type: str) -> tuple[bytes, float | None, float | None]:
    """Extract GPS coordinates from EXIF then strip all EXIF from the image.

    Pillow is optional; when unavailable (or the bytes are not a decodable
    image) the original content is returned untouched.
    """
    if mime_type not in _IMAGE_MIME_TYPES:
        return content, None, None
    try:
        from PIL import Image  # type: ignore[import-not-found]
        from PIL.ExifTags import GPSTAGS  # type: ignore[import-not-found]
    except ImportError:
        return content, None, None
    try:
        image = Image.open(io.BytesIO(content))
        latitude = longitude = None
        exif = image.getexif()
        if exif:
            gps_ifd = exif.get_ifd(0x8825)
            if gps_ifd:
                gps = {GPSTAGS.get(tag, tag): value for tag, value in gps_ifd.items()}
                lat = _exif_gps_to_degrees(gps.get("GPSLatitude"))
                lng = _exif_gps_to_degrees(gps.get("GPSLongitude"))
                if lat is not None and str(gps.get("GPSLatitudeRef", "N")).upper() == "S":
                    lat = -lat
                if lng is not None and str(gps.get("GPSLongitudeRef", "E")).upper() == "W":
                    lng = -lng
                latitude, longitude = lat, lng
        # Re-encode without EXIF so stored photos carry no embedded metadata.
        cleaned = io.BytesIO()
        stripped = Image.new(image.mode, image.size)
        stripped.putdata(list(image.getdata()))
        save_format = image.format or "JPEG"
        stripped.save(cleaned, format=save_format)
        return cleaned.getvalue(), latitude, longitude
    except Exception:
        logger.warning("field_attachment_exif_processing_failed mime=%s", mime_type)
        return content, None, None


def _coerce_captured_at(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid captured_at timestamp") from exc


def _resolve_governing_work_order_id(db: Session, attachment: FieldAttachment):
    if attachment.work_order_id is not None:
        return attachment.work_order_id
    if attachment.note_id is not None:
        note = db.get(WorkOrderNote, attachment.note_id)
        return note.work_order_id if note else None
    return None


def _caller_can_access(
    db: Session,
    person_id: str | None,
    *,
    work_order_id=None,
    installation_project_id=None,
) -> bool:
    """True if the caller may touch an attachment governed by this work order
    (staff assignment) or installation project (their vendor)."""
    if person_id is None:
        return False
    from app.services.field.jobs import caller_can_access

    if work_order_id is not None:
        work_order = db.get(WorkOrder, work_order_id)
        if work_order and caller_can_access(db, person_id, work_order):
            return True
    if installation_project_id is not None:
        from app.services.vendor_portal import get_vendor_user

        vendor_user = get_vendor_user(db, person_id)
        if vendor_user:
            project = db.get(InstallationProject, installation_project_id)
            if project and project.assigned_vendor_id == vendor_user.vendor_id:
                return True
    return False


def _assert_attachment_access(db: Session, person_id: str | None, attachment: FieldAttachment) -> None:
    """Uniform 404 (no existence leak) when the caller isn't on the attachment's
    work order / project."""
    if _caller_can_access(
        db,
        person_id,
        work_order_id=_resolve_governing_work_order_id(db, attachment),
        installation_project_id=attachment.installation_project_id,
    ):
        return
    raise HTTPException(status_code=404, detail="Attachment not found")


class FieldAttachments(ListResponseMixin):
    @staticmethod
    def create(
        db: Session,
        *,
        kind: str | FieldAttachmentKind,
        file_name: str,
        mime_type: str,
        content: bytes,
        client_ref: str | None = None,
        work_order_id: str | None = None,
        installation_project_id: str | None = None,
        note_id: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        captured_at: str | datetime | None = None,
        signer_name: str | None = None,
        uploaded_by_person_id: str | None = None,
        uploaded_by_vendor_user_id: str | None = None,
    ) -> FieldAttachment:
        kind_value = validate_enum(
            kind.value if isinstance(kind, FieldAttachmentKind) else kind, FieldAttachmentKind, "kind"
        )

        # Validate size BEFORE any write.
        if len(content) == 0:
            raise HTTPException(status_code=422, detail="Empty file")
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds maximum size of 5 MB")
        normalized_mime = (mime_type or "").lower().split(";")[0].strip()
        if normalized_mime not in ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=415, detail=f"Unsupported file type: {normalized_mime or 'unknown'}")

        if not any([work_order_id, installation_project_id, note_id]):
            raise HTTPException(
                status_code=422,
                detail="Attachment must reference a work order, installation project, or note",
            )

        # Offline idempotency: a retried upload with the same client_ref
        # returns the original attachment instead of storing a duplicate.
        client_ref_uuid = coerce_uuid(client_ref) if client_ref else None
        if client_ref_uuid:
            existing = db.query(FieldAttachment).filter(FieldAttachment.client_ref == client_ref_uuid).first()
            if existing:
                return existing

        work_order_uuid = coerce_uuid(work_order_id) if work_order_id else None
        if work_order_uuid and not db.get(WorkOrder, work_order_uuid):
            raise HTTPException(status_code=404, detail="Work order not found")
        project_uuid = coerce_uuid(installation_project_id) if installation_project_id else None
        if project_uuid and not db.get(InstallationProject, project_uuid):
            raise HTTPException(status_code=404, detail="Installation project not found")
        note_uuid = coerce_uuid(note_id) if note_id else None
        note_obj = db.get(WorkOrderNote, note_uuid) if note_uuid else None
        if note_uuid and not note_obj:
            raise HTTPException(status_code=404, detail="Work order note not found")

        # The caller must be assigned to the governing work order / own the
        # vendor project — otherwise it's a foreign job (uniform 404).
        governing_wo = work_order_uuid or (note_obj.work_order_id if note_obj else None)
        if not _caller_can_access(
            db,
            uploaded_by_person_id,
            work_order_id=governing_wo,
            installation_project_id=project_uuid,
        ):
            raise HTTPException(status_code=404, detail="Job not found")

        processed, exif_lat, exif_lng = _process_image(content, normalized_mime)
        storage_key = f"{_STORAGE_PREFIX}/{uuid.uuid4().hex}{_safe_extension(file_name)}"
        storage.put(storage_key, processed, normalized_mime)

        attachment = FieldAttachment(
            work_order_id=work_order_uuid,
            installation_project_id=project_uuid,
            note_id=note_uuid,
            kind=kind_value,
            storage_key=storage_key,
            file_name=file_name[:255],
            mime_type=normalized_mime,
            size_bytes=len(processed),
            latitude=latitude if latitude is not None else exif_lat,
            longitude=longitude if longitude is not None else exif_lng,
            captured_at=_coerce_captured_at(captured_at),
            signer_name=signer_name,
            uploaded_by_person_id=coerce_uuid(uploaded_by_person_id) if uploaded_by_person_id else None,
            uploaded_by_vendor_user_id=(
                coerce_uuid(uploaded_by_vendor_user_id) if uploaded_by_vendor_user_id else None
            ),
            client_ref=client_ref_uuid,
        )
        db.add(attachment)
        try:
            db.commit()
        except IntegrityError:
            # Concurrent retry raced us on client_ref: serve the winner's row.
            db.rollback()
            storage.delete(storage_key)
            existing = db.query(FieldAttachment).filter(FieldAttachment.client_ref == client_ref_uuid).first()
            if existing:
                return existing
            raise
        db.refresh(attachment)
        return attachment

    @staticmethod
    def get(db: Session, attachment_id: str, caller_person_id: str | None = None) -> FieldAttachment:
        attachment = db.get(FieldAttachment, coerce_uuid(attachment_id))
        if not attachment or not attachment.is_active:
            raise HTTPException(status_code=404, detail="Attachment not found")
        _assert_attachment_access(db, caller_person_id, attachment)
        return attachment

    @staticmethod
    def get_content(
        db: Session, attachment_id: str, caller_person_id: str | None = None
    ) -> tuple[FieldAttachment, bytes]:
        attachment = FieldAttachments.get(db, attachment_id, caller_person_id)
        try:
            content = storage.get(attachment.storage_key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Attachment content not found") from exc
        return attachment, content

    @staticmethod
    def list(
        db: Session,
        *,
        caller_person_id: str | None = None,
        work_order_id: str | None = None,
        installation_project_id: str | None = None,
        note_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FieldAttachment]:
        # No unscoped global listing: a caller must name a container they can
        # access, and we verify access before returning anything.
        note_wo_id = None
        if note_id and not work_order_id:
            note = db.get(WorkOrderNote, coerce_uuid(note_id))
            note_wo_id = note.work_order_id if note else None
        if not _caller_can_access(
            db,
            caller_person_id,
            work_order_id=coerce_uuid(work_order_id) if work_order_id else note_wo_id,
            installation_project_id=coerce_uuid(installation_project_id) if installation_project_id else None,
        ):
            raise HTTPException(status_code=404, detail="Job not found")

        query = db.query(FieldAttachment).filter(FieldAttachment.is_active.is_(True))
        if work_order_id:
            query = query.filter(FieldAttachment.work_order_id == coerce_uuid(work_order_id))
        if installation_project_id:
            query = query.filter(FieldAttachment.installation_project_id == coerce_uuid(installation_project_id))
        if note_id:
            query = query.filter(FieldAttachment.note_id == coerce_uuid(note_id))
        if kind:
            query = query.filter(FieldAttachment.kind == validate_enum(kind, FieldAttachmentKind, "kind"))
        query = query.order_by(FieldAttachment.created_at.desc())
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def delete(db: Session, attachment_id: str, caller_person_id: str | None = None) -> None:
        attachment = FieldAttachments.get(db, attachment_id, caller_person_id)
        attachment.is_active = False
        db.commit()


field_attachments = FieldAttachments()
