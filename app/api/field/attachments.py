from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.field import FieldAttachmentRead
from app.services.auth_dependencies import require_user_auth
from app.services.field import field_attachments

router = APIRouter(tags=["field-attachments"])


@router.post(
    "/attachments",
    response_model=FieldAttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_field_attachment(
    file: UploadFile = File(...),
    kind: str = Form(default="photo"),
    client_ref: str | None = Form(default=None),
    work_order_id: str | None = Form(default=None),
    installation_project_id: str | None = Form(default=None),
    note_id: str | None = Form(default=None),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    captured_at: str | None = Form(default=None),
    signer_name: str | None = Form(default=None),
    asset_type: str | None = Form(default=None),
    asset_id: str | None = Form(default=None),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_attachments.create(
        db,
        kind=kind,
        file_name=file.filename or "upload",
        mime_type=file.content_type or "",
        content=file.file.read(),
        client_ref=client_ref,
        work_order_id=work_order_id,
        installation_project_id=installation_project_id,
        note_id=note_id,
        latitude=latitude,
        longitude=longitude,
        captured_at=captured_at,
        signer_name=signer_name,
        uploaded_by_person_id=auth["person_id"],
        asset_type=asset_type,
        asset_id=asset_id,
    )


@router.get("/attachments", response_model=ListResponse[FieldAttachmentRead])
def list_field_attachments(
    work_order_id: str | None = None,
    installation_project_id: str | None = None,
    note_id: str | None = None,
    kind: str | None = None,
    asset_type: str | None = None,
    asset_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    items = field_attachments.list(
        db,
        caller_person_id=auth["person_id"],
        work_order_id=work_order_id,
        installation_project_id=installation_project_id,
        note_id=note_id,
        kind=kind,
        asset_type=asset_type,
        asset_id=asset_id,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "count": len(items), "limit": limit, "offset": offset}


@router.get("/attachments/{attachment_id}", response_model=FieldAttachmentRead)
def get_field_attachment(attachment_id: str, auth=Depends(require_user_auth), db: Session = Depends(get_db)):
    return field_attachments.get(db, attachment_id, caller_person_id=auth["person_id"])


@router.get("/attachments/{attachment_id}/content")
def download_field_attachment(attachment_id: str, auth=Depends(require_user_auth), db: Session = Depends(get_db)):
    attachment, content = field_attachments.get_content(db, attachment_id, caller_person_id=auth["person_id"])
    return Response(
        content=content,
        media_type=attachment.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{attachment.file_name}"'},
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_field_attachment(attachment_id: str, auth=Depends(require_user_auth), db: Session = Depends(get_db)):
    field_attachments.delete(db, attachment_id, caller_person_id=auth["person_id"])
