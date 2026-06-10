from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.field import (
    FieldAttachmentRead,
    FieldCustomer,
    FieldJobDetail,
    FieldJobLocation,
    FieldJobSummary,
    FieldMaterialRead,
    FieldMeResponse,
    FieldNoteRead,
    FieldWorkLogRead,
)
from app.services.auth_dependencies import require_user_auth
from app.services.field.jobs import field_jobs

router = APIRouter(tags=["field-jobs"])


@router.get("/me", response_model=FieldMeResponse)
def field_me(auth=Depends(require_user_auth), db: Session = Depends(get_db)):
    return field_jobs.me(db, auth["person_id"])


@router.get("/jobs", response_model=ListResponse[FieldJobSummary])
def list_field_jobs(
    status: str | None = None,
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    work_orders = field_jobs.list(
        db,
        auth["person_id"],
        status=status,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    items = [FieldJobSummary.from_work_order(wo) for wo in work_orders]
    return {"items": items, "count": len(items), "limit": limit, "offset": offset}


@router.get("/jobs/{work_order_id}", response_model=FieldJobDetail)
def get_field_job(work_order_id: str, auth=Depends(require_user_auth), db: Session = Depends(get_db)):
    bundle = field_jobs.get_detail(db, auth["person_id"], work_order_id)
    return FieldJobDetail(
        job=FieldJobSummary.from_work_order(bundle["work_order"]),
        customer=FieldCustomer(**bundle["customer"]) if bundle["customer"] else None,
        location=FieldJobLocation(**bundle["location"]),
        ticket_ref=bundle["ticket_ref"],
        project_id=bundle["project_id"],
        notes=[FieldNoteRead.model_validate(n) for n in bundle["notes"]],
        attachments=[FieldAttachmentRead.model_validate(a) for a in bundle["attachments"]],
        materials=[FieldMaterialRead.from_material(m) for m in bundle["materials"]],
        worklogs=[FieldWorkLogRead.model_validate(w) for w in bundle["worklogs"]],
    )
