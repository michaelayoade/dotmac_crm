from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.field import FieldNoteCreate, FieldNoteRead
from app.services.auth_dependencies import require_user_auth
from app.services.field.notes import field_notes

router = APIRouter(tags=["field-notes"])


@router.post(
    "/jobs/{work_order_id}/notes",
    response_model=FieldNoteRead,
    status_code=status.HTTP_201_CREATED,
)
def create_field_note(
    work_order_id: str,
    payload: FieldNoteCreate,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_notes.create(
        db,
        auth["person_id"],
        work_order_id,
        body=payload.body,
        attachment_ids=[str(a) for a in payload.attachment_ids],
    )
