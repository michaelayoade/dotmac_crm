from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.field import FieldJobSummary, FieldTransitionRequest, FieldTransitionResponse
from app.services.auth_dependencies import require_user_auth
from app.services.field.transitions import field_transitions

router = APIRouter(tags=["field-transitions"])


@router.post("/jobs/{work_order_id}/transition", response_model=FieldTransitionResponse)
def transition_field_job(
    work_order_id: str,
    payload: FieldTransitionRequest,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    result = field_transitions.apply(
        db,
        auth["person_id"],
        work_order_id,
        event=payload.event,
        client_event_id=str(payload.client_event_id),
        occurred_at=payload.occurred_at,
        latitude=payload.latitude,
        longitude=payload.longitude,
        note=payload.note,
        payload=payload.payload,
    )
    return FieldTransitionResponse(
        job=FieldJobSummary.from_work_order(result["work_order"]),
        event=result["event"].event.value,
        event_id=result["event"].id,
        replayed=result["replayed"],
    )
