from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_db
from app.models.expense_request import ExpenseRequest, ExpenseRequestStatus
from app.models.person import Person
from app.models.subscriber import Subscriber
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.schemas.workforce import WorkOrderUpdate
from app.services import workforce as workforce_service
from app.services.auth_dependencies import require_any_permission, require_permission
from app.services.common import coerce_uuid
from app.services.expense_requests import expense_requests
from app.services.field.location import cached_job_location
from app.services.field.location_tracking import field_location_tracking

router = APIRouter(prefix="/manager", tags=["field-manager"])

_manager_access = require_any_permission(
    "operations:work_order:read",
    "operations:technician:read",
    "operations:expense_request:read",
)
_ops_read = require_any_permission(
    "operations:work_order:read",
    "operations:technician:read",
)
_expense_read = require_permission("operations:expense_request:read")
_dispatch_write = require_any_permission(
    "operations:work_order:update",
    "operations:work_order:dispatch",
)
_expense_write = require_permission("operations:expense_request:write")


class ManagerJobAssignRequest(BaseModel):
    person_id: str = Field(min_length=1)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    status: str | None = None


class ManagerExpenseRejectRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


def _person_label(person: Person | None) -> str | None:
    if person is None:
        return None
    if person.display_name:
        return person.display_name
    name = f"{person.first_name or ''} {person.last_name or ''}".strip()
    return name or person.email


def _enum_value(value) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", value)


def _subscriber_label(subscriber: Subscriber | None) -> str | None:
    if subscriber is None:
        return None
    account = subscriber.account_number or subscriber.subscriber_number
    label = subscriber.display_name
    if account and account != label:
        return f"{label} ({account})"
    return label


def _job_payload(work_order: WorkOrder) -> dict:
    location = cached_job_location(work_order) or {}
    ticket = work_order.ticket
    project = work_order.project
    return {
        "id": str(work_order.id),
        "title": work_order.title,
        "description": work_order.description,
        "status": _enum_value(work_order.status),
        "priority": _enum_value(work_order.priority),
        "work_type": _enum_value(work_order.work_type),
        "scheduled_start": work_order.scheduled_start,
        "scheduled_end": work_order.scheduled_end,
        "assigned_to_person_id": str(work_order.assigned_to_person_id) if work_order.assigned_to_person_id else None,
        "assigned_to_label": _person_label(work_order.assigned_to) if work_order.assigned_to_person_id else None,
        "subscriber_label": _subscriber_label(work_order.subscriber),
        "ticket_label": (ticket.number or ticket.title) if ticket else None,
        "project_label": project.name if project else None,
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "address_text": location.get("address_text"),
        "location_source": location.get("source"),
    }


def _response(payload: dict) -> JSONResponse:
    return JSONResponse(jsonable_encoder(payload))


@router.get("/me")
def manager_me(auth=Depends(_manager_access), db: Session = Depends(get_db)):
    person = db.get(Person, coerce_uuid(auth["person_id"]))
    return _response(
        {
            "person_id": auth["person_id"],
            "name": _person_label(person) or "Manager",
            "roles": auth.get("roles") or [],
            "permissions": auth.get("scopes") or [],
            "is_manager": True,
        }
    )


@router.get("/summary")
def manager_summary(
    stale_after_seconds: int = Query(default=120, ge=30, le=3600),
    auth=Depends(_ops_read),
    db: Session = Depends(get_db),
):
    technicians = field_location_tracking.list_tracking_states(
        db,
        stale_after_seconds=stale_after_seconds,
        limit=500,
    )
    live_count = sum(1 for item in technicians if item.get("is_live"))
    sharing_count = sum(1 for item in technicians if item.get("location_sharing_enabled"))
    open_statuses = [
        WorkOrderStatus.scheduled,
        WorkOrderStatus.dispatched,
        WorkOrderStatus.in_progress,
        WorkOrderStatus.paused,
    ]
    open_jobs = (
        db.query(WorkOrder).filter(WorkOrder.is_active.is_(True)).filter(WorkOrder.status.in_(open_statuses)).count()
    )
    unassigned_jobs = (
        db.query(WorkOrder)
        .filter(WorkOrder.is_active.is_(True))
        .filter(WorkOrder.status.in_(open_statuses))
        .filter(WorkOrder.assigned_to_person_id.is_(None))
        .count()
    )
    submitted_expenses = (
        db.query(ExpenseRequest)
        .filter(ExpenseRequest.is_active.is_(True))
        .filter(ExpenseRequest.status == ExpenseRequestStatus.submitted)
        .count()
    )
    return _response(
        {
            "technicians_total": len(technicians),
            "technicians_live": live_count,
            "technicians_sharing": sharing_count,
            "open_jobs": open_jobs,
            "unassigned_jobs": unassigned_jobs,
            "pending_expenses": submitted_expenses,
        }
    )


@router.get("/technicians")
def manager_technicians(
    stale_after_seconds: int = Query(default=120, ge=30, le=3600),
    limit: int = Query(default=500, ge=1, le=500),
    auth=Depends(_ops_read),
    db: Session = Depends(get_db),
):
    items = field_location_tracking.list_tracking_states(
        db,
        stale_after_seconds=stale_after_seconds,
        limit=limit,
    )
    return _response(
        {
            "items": items,
            "count": len(items),
            "live_count": sum(1 for item in items if item.get("is_live")),
            "sharing_count": sum(1 for item in items if item.get("location_sharing_enabled")),
            "limit": limit,
            "offset": 0,
        }
    )


@router.get("/jobs")
def manager_jobs(
    status: str | None = None,
    assigned_to_person_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth=Depends(_ops_read),
    db: Session = Depends(get_db),
):
    query = (
        db.query(WorkOrder)
        .options(
            joinedload(WorkOrder.assigned_to),
            joinedload(WorkOrder.subscriber).joinedload(Subscriber.person),
            joinedload(WorkOrder.subscriber).joinedload(Subscriber.organization),
            joinedload(WorkOrder.ticket),
            joinedload(WorkOrder.project),
        )
        .filter(WorkOrder.is_active.is_(True))
    )
    if status:
        query = query.filter(WorkOrder.status == WorkOrderStatus(status))
    else:
        query = query.filter(
            WorkOrder.status.in_(
                [
                    WorkOrderStatus.scheduled,
                    WorkOrderStatus.dispatched,
                    WorkOrderStatus.in_progress,
                    WorkOrderStatus.paused,
                ]
            )
        )
    if assigned_to_person_id:
        query = query.filter(WorkOrder.assigned_to_person_id == coerce_uuid(assigned_to_person_id))
    rows = (
        query.order_by(
            WorkOrder.scheduled_start.asc().nullslast(),
            WorkOrder.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
        .all()
    )
    return _response(
        {
            "items": [_job_payload(row) for row in rows],
            "count": len(rows),
            "limit": limit,
            "offset": offset,
        }
    )


@router.post("/jobs/{work_order_id}/assign")
def manager_assign_job(
    work_order_id: str,
    payload: ManagerJobAssignRequest,
    auth=Depends(_dispatch_write),
    db: Session = Depends(get_db),
):
    status = payload.status or WorkOrderStatus.dispatched.value
    work_order = workforce_service.work_orders.update(
        db,
        work_order_id,
        WorkOrderUpdate(
            assigned_to_person_id=coerce_uuid(payload.person_id),
            scheduled_start=payload.scheduled_start,
            scheduled_end=payload.scheduled_end,
            status=WorkOrderStatus(status),
        ),
    )
    return _response(_job_payload(work_order))


@router.get("/expenses")
def manager_expenses(
    status: str | None = Query(default=ExpenseRequestStatus.submitted.value),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth=Depends(_expense_read),
    db: Session = Depends(get_db),
):
    items = expense_requests.list(db, status=status, limit=limit, offset=offset)
    return _response(
        {
            "items": items,
            "count": len(items),
            "limit": limit,
            "offset": offset,
        }
    )


@router.post("/expenses/{expense_request_id}/approve")
def manager_approve_expense(
    expense_request_id: str,
    auth=Depends(_expense_write),
    db: Session = Depends(get_db),
):
    item = expense_requests.approve(db, expense_request_id)
    return _response(jsonable_encoder(item))


@router.post("/expenses/{expense_request_id}/reject")
def manager_reject_expense(
    expense_request_id: str,
    payload: ManagerExpenseRejectRequest,
    auth=Depends(_expense_write),
    db: Session = Depends(get_db),
):
    item = expense_requests.reject(db, expense_request_id, payload.reason)
    return _response(jsonable_encoder(item))
