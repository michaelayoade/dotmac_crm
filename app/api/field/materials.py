from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.field import FieldMaterialConsumeRequest, FieldMaterialRead
from app.services.auth_dependencies import require_user_auth
from app.services.field.materials import field_materials

router = APIRouter(tags=["field-materials"])


@router.get("/jobs/{work_order_id}/materials", response_model=list[FieldMaterialRead])
def list_job_materials(work_order_id: str, auth=Depends(require_user_auth), db: Session = Depends(get_db)):
    materials = field_materials.list_for_job(db, auth["person_id"], work_order_id)
    return [FieldMaterialRead.from_material(m) for m in materials]


@router.post("/jobs/{work_order_id}/materials/consume", response_model=list[FieldMaterialRead])
def consume_job_materials(
    work_order_id: str,
    payload: FieldMaterialConsumeRequest,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    materials = field_materials.consume(
        db,
        auth["person_id"],
        work_order_id,
        [item.model_dump() for item in payload.items],
    )
    return [FieldMaterialRead.from_material(m) for m in materials]
