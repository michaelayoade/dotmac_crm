"""Field note creation — wraps the workforce note service and links evidence."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.field import FieldAttachment
from app.models.workforce import WorkOrderNote
from app.schemas.workforce import WorkOrderNoteCreate
from app.services.common import coerce_uuid
from app.services.field.jobs import get_scoped_work_order
from app.services.workforce import work_order_notes


class FieldNotes:
    @staticmethod
    def create(
        db: Session,
        person_id: str,
        work_order_id: str,
        *,
        body: str,
        attachment_ids: list[str] | None = None,
    ) -> WorkOrderNote:
        work_order = get_scoped_work_order(db, person_id, work_order_id)
        person_uuid = coerce_uuid(person_id)
        if not body or not body.strip():
            raise HTTPException(status_code=422, detail="Note body is required")

        # Validate attachment links BEFORE creating the note: each must be an
        # active attachment the caller uploaded to this same work order.
        attachments: list[FieldAttachment] = []
        for attachment_id in attachment_ids or []:
            attachment = db.get(FieldAttachment, coerce_uuid(attachment_id))
            if not attachment or not attachment.is_active:
                raise HTTPException(status_code=404, detail="Attachment not found")
            if attachment.work_order_id != work_order.id:
                raise HTTPException(status_code=422, detail="Attachment belongs to a different job")
            if attachment.uploaded_by_person_id != person_uuid:
                raise HTTPException(status_code=403, detail="Attachment uploaded by someone else")
            attachments.append(attachment)

        note = work_order_notes.create(
            db,
            WorkOrderNoteCreate(
                work_order_id=work_order.id,
                body=body.strip(),
                author_person_id=person_uuid,
            ),
        )
        for attachment in attachments:
            attachment.note_id = note.id
        if attachments:
            db.commit()
            db.refresh(note)
        return note


field_notes = FieldNotes()
