from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.field import FieldEquipmentRead, FieldEquipmentRecord
from app.services.auth_dependencies import require_user_auth
from app.services.field.equipment import field_equipment

router = APIRouter(tags=["field-equipment"])


@router.post(
    "/jobs/{work_order_id}/equipment",
    response_model=FieldEquipmentRead,
    status_code=status.HTTP_201_CREATED,
)
def record_job_equipment(
    work_order_id: str,
    payload: FieldEquipmentRecord,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    assignment = field_equipment.record(
        db,
        auth["person_id"],
        work_order_id,
        serial_number=payload.serial_number,
        vendor=payload.vendor,
        model=payload.model,
        notes=payload.notes,
    )
    return FieldEquipmentRead.from_assignment(assignment)


@router.get("/jobs/{work_order_id}/equipment", response_model=FieldEquipmentRead | None)
def get_job_equipment(work_order_id: str, auth=Depends(require_user_auth), db: Session = Depends(get_db)):
    assignment = field_equipment.current_for_job(db, auth["person_id"], work_order_id)
    return FieldEquipmentRead.from_assignment(assignment) if assignment else None
