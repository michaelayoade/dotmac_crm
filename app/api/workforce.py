from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.common import ListResponse
from app.schemas.workforce import (
    WorkOrderAssignmentCreate,
    WorkOrderAssignmentRead,
    WorkOrderAssignmentUpdate,
    WorkOrderCreate,
    WorkOrderNoteCreate,
    WorkOrderNoteRead,
    WorkOrderNoteUpdate,
    WorkOrderRead,
    WorkOrderUpdate,
)
from app.schemas.timecost import CostSummary
from app.services import workforce as workforce_service
from app.services import timecost as timecost_service

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post(
    "/work-orders",
    response_model=WorkOrderRead,
    status_code=status.HTTP_201_CREATED,
    tags=["work-orders"],
)
def create_work_order(payload: WorkOrderCreate, db: Session = Depends(get_db)):
    return workforce_service.work_orders.create(db, payload)


@router.get("/work-orders/{work_order_id}", response_model=WorkOrderRead, tags=["work-orders"])
def get_work_order(work_order_id: str, db: Session = Depends(get_db)):
    return workforce_service.work_orders.get(db, work_order_id)


@router.get("/work-orders", response_model=ListResponse[WorkOrderRead], tags=["work-orders"])
def list_work_orders(
    account_id: str | None = None,
    subscription_id: str | None = None,
    service_order_id: str | None = None,
    ticket_id: str | None = None,
    project_id: str | None = None,
    assigned_to_person_id: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    work_type: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return workforce_service.work_orders.list_response(
        db,
        account_id,
        subscription_id,
        service_order_id,
        ticket_id,
        project_id,
        assigned_to_person_id,
        status,
        priority,
        work_type,
        is_active,
        order_by,
        order_dir,
        limit,
        offset,
    )


@router.patch(
    "/work-orders/{work_order_id}", response_model=WorkOrderRead, tags=["work-orders"]
)
def update_work_order(
    work_order_id: str, payload: WorkOrderUpdate, db: Session = Depends(get_db)
):
    return workforce_service.work_orders.update(db, work_order_id, payload)


@router.delete(
    "/work-orders/{work_order_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["work-orders"],
)
def delete_work_order(work_order_id: str, db: Session = Depends(get_db)):
    workforce_service.work_orders.delete(db, work_order_id)


@router.get(
    "/work-orders/{work_order_id}/cost-summary",
    response_model=CostSummary,
    tags=["work-orders"],
)
def work_order_cost_summary(work_order_id: str, db: Session = Depends(get_db)):
    return timecost_service.work_order_cost_summary(db, work_order_id)


@router.post(
    "/work-order-assignments",
    response_model=WorkOrderAssignmentRead,
    status_code=status.HTTP_201_CREATED,
    tags=["work-order-assignments"],
)
def create_work_order_assignment(
    payload: WorkOrderAssignmentCreate, db: Session = Depends(get_db)
):
    return workforce_service.work_order_assignments.create(db, payload)


@router.get(
    "/work-order-assignments/{assignment_id}",
    response_model=WorkOrderAssignmentRead,
    tags=["work-order-assignments"],
)
def get_work_order_assignment(assignment_id: str, db: Session = Depends(get_db)):
    return workforce_service.work_order_assignments.get(db, assignment_id)


@router.get(
    "/work-order-assignments",
    response_model=ListResponse[WorkOrderAssignmentRead],
    tags=["work-order-assignments"],
)
def list_work_order_assignments(
    work_order_id: str | None = None,
    person_id: str | None = None,
    order_by: str = Query(default="assigned_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return workforce_service.work_order_assignments.list_response(
        db, work_order_id, person_id, order_by, order_dir, limit, offset
    )


@router.patch(
    "/work-order-assignments/{assignment_id}",
    response_model=WorkOrderAssignmentRead,
    tags=["work-order-assignments"],
)
def update_work_order_assignment(
    assignment_id: str, payload: WorkOrderAssignmentUpdate, db: Session = Depends(get_db)
):
    return workforce_service.work_order_assignments.update(db, assignment_id, payload)


@router.delete(
    "/work-order-assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["work-order-assignments"],
)
def delete_work_order_assignment(assignment_id: str, db: Session = Depends(get_db)):
    workforce_service.work_order_assignments.delete(db, assignment_id)


@router.post(
    "/work-order-notes",
    response_model=WorkOrderNoteRead,
    status_code=status.HTTP_201_CREATED,
    tags=["work-order-notes"],
)
def create_work_order_note(payload: WorkOrderNoteCreate, db: Session = Depends(get_db)):
    return workforce_service.work_order_notes.create(db, payload)


@router.get(
    "/work-order-notes/{note_id}",
    response_model=WorkOrderNoteRead,
    tags=["work-order-notes"],
)
def get_work_order_note(note_id: str, db: Session = Depends(get_db)):
    return workforce_service.work_order_notes.get(db, note_id)


@router.get(
    "/work-order-notes",
    response_model=ListResponse[WorkOrderNoteRead],
    tags=["work-order-notes"],
)
def list_work_order_notes(
    work_order_id: str | None = None,
    is_internal: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return workforce_service.work_order_notes.list_response(
        db, work_order_id, is_internal, order_by, order_dir, limit, offset
    )


@router.patch(
    "/work-order-notes/{note_id}",
    response_model=WorkOrderNoteRead,
    tags=["work-order-notes"],
)
def update_work_order_note(
    note_id: str, payload: WorkOrderNoteUpdate, db: Session = Depends(get_db)
):
    return workforce_service.work_order_notes.update(db, note_id, payload)


@router.delete(
    "/work-order-notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["work-order-notes"],
)
def delete_work_order_note(note_id: str, db: Session = Depends(get_db)):
    workforce_service.work_order_notes.delete(db, note_id)
