from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.field import FieldSpliceCreate, FieldSpliceProposalResponse
from app.services.auth_dependencies import require_user_auth
from app.services.field.fiber import propose_splice

router = APIRouter(prefix="/fiber", tags=["field-fiber"])


@router.post("/splices", response_model=FieldSpliceProposalResponse, status_code=201)
def propose_field_splice(
    payload: FieldSpliceCreate,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return propose_splice(
        db,
        auth.get("person_id"),
        closure_id=str(payload.closure_id),
        from_strand_id=str(payload.from_strand_id),
        to_strand_id=str(payload.to_strand_id),
        tray_id=str(payload.tray_id) if payload.tray_id else None,
        position=payload.position,
        splice_type=payload.splice_type,
        loss_db=payload.loss_db,
        note=payload.note,
    )
