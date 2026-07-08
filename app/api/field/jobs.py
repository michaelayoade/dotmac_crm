from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.field import (
    FieldAttachmentRead,
    FieldCustomer,
    FieldJobChatMessage,
    FieldJobChatMessageCreate,
    FieldJobChatResponse,
    FieldJobDestination,
    FieldJobDestinationsResponse,
    FieldJobDetail,
    FieldJobHistoryItem,
    FieldJobLocation,
    FieldJobLocationUpdate,
    FieldJobSummary,
    FieldMaterialRead,
    FieldMeResponse,
    FieldNoteRead,
    FieldOpenTicketItem,
    FieldSiteContact,
    FieldVisitHistoryItem,
    FieldWorkLogRead,
)
from app.schemas.material_request import MaterialRequestRead
from app.services.auth_dependencies import require_user_auth
from app.services.field import chat as field_chat
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
        access_notes=bundle["access_notes"],
        additional_contacts=[FieldSiteContact(**c) for c in bundle["additional_contacts"]],
        recent_visits=[FieldVisitHistoryItem(**v) for v in bundle["recent_visits"]],
        open_tickets=[FieldOpenTicketItem(**t) for t in bundle["open_tickets"]],
        notes=[FieldNoteRead.from_note(n) for n in bundle["notes"]],
        attachments=[FieldAttachmentRead.model_validate(a) for a in bundle["attachments"]],
        materials=[FieldMaterialRead.from_material(m) for m in bundle["materials"]],
        material_requests=[MaterialRequestRead.model_validate(mr) for mr in bundle["material_requests"]],
        worklogs=[FieldWorkLogRead.model_validate(w) for w in bundle["worklogs"]],
        history=[FieldJobHistoryItem(**item) for item in bundle["history"]],
    )


@router.get("/jobs/{work_order_id}/destinations", response_model=FieldJobDestinationsResponse)
def list_field_job_destinations(work_order_id: str, auth=Depends(require_user_auth), db: Session = Depends(get_db)):
    items = field_jobs.list_destinations(db, auth["person_id"], work_order_id)
    return FieldJobDestinationsResponse(
        items=[FieldJobDestination(**item) for item in items],
        count=len(items),
    )


@router.get("/jobs/{work_order_id}/chat", response_model=FieldJobChatResponse)
def get_field_job_chat(work_order_id: str, auth=Depends(require_user_auth), db: Session = Depends(get_db)):
    payload = field_chat.get_job_chat(db, auth["person_id"], work_order_id)
    return FieldJobChatResponse(
        available=payload["available"],
        can_send=payload["can_send"],
        conversation_id=payload["conversation_id"],
        customer_name=payload["customer_name"],
        messages=[FieldJobChatMessage(**message) for message in payload["messages"]],
    )


@router.post("/jobs/{work_order_id}/chat/messages", response_model=FieldJobChatMessage)
def send_field_job_chat_message(
    work_order_id: str,
    payload: FieldJobChatMessageCreate,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    message = field_chat.send_job_chat_message(
        db,
        auth["person_id"],
        work_order_id,
        body=payload.body,
    )
    return FieldJobChatMessage(**message)


@router.patch("/jobs/{work_order_id}/location", response_model=FieldJobLocation)
def update_field_job_location(
    work_order_id: str,
    payload: FieldJobLocationUpdate,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    location = field_jobs.update_location(
        db,
        auth["person_id"],
        work_order_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    return FieldJobLocation(**location)
