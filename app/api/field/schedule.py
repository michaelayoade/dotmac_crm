from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.auth_dependencies import require_user_auth
from app.services.field.schedule import field_schedule

router = APIRouter(tags=["field-schedule"])


class FieldScheduleEntry(BaseModel):
    type: str  # shift | availability | job
    start_at: datetime
    end_at: datetime | None
    title: str
    reference_id: UUID


@router.get("/schedule", response_model=list[FieldScheduleEntry])
def get_field_schedule(
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_schedule.timeline(db, auth["person_id"], date_from=date_from, date_to=date_to)
