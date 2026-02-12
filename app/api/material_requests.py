from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.material_request import (
    MaterialRequestCreate,
    MaterialRequestItemCreate,
    MaterialRequestItemRead,
    MaterialRequestRead,
    MaterialRequestUpdate,
)
from app.services.material_requests import material_requests
from app.services.response import list_response

router = APIRouter(prefix="/material-requests", tags=["material-requests"])


@router.post("", response_model=MaterialRequestRead, status_code=status.HTTP_201_CREATED)
def create_material_request(payload: MaterialRequestCreate, db: Session = Depends(get_db)):
    return material_requests.create(db, payload)


@router.get("/{mr_id}", response_model=MaterialRequestRead)
def get_material_request(mr_id: str, db: Session = Depends(get_db)):
    return material_requests.get(db, mr_id)


@router.get("", response_model=ListResponse[MaterialRequestRead])
def list_material_requests(
    is_active: bool | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    ticket_id: str | None = None,
    project_id: str | None = None,
    priority: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = material_requests.list(
        db,
        is_active=is_active,
        status=status_filter,
        ticket_id=ticket_id,
        project_id=project_id,
        priority=priority,
        order_by=order_by,
        order_dir=order_dir,
        limit=limit,
        offset=offset,
    )
    return list_response(items, limit, offset)


@router.patch("/{mr_id}", response_model=MaterialRequestRead)
def update_material_request(mr_id: str, payload: MaterialRequestUpdate, db: Session = Depends(get_db)):
    return material_requests.update(db, mr_id, payload)


# ── Status transitions ──────────────────────────────────────────


@router.post("/{mr_id}/submit", response_model=MaterialRequestRead)
def submit_material_request(mr_id: str, db: Session = Depends(get_db)):
    return material_requests.submit(db, mr_id)


@router.post("/{mr_id}/approve", response_model=MaterialRequestRead)
def approve_material_request(mr_id: str, approved_by_person_id: str = Query(), db: Session = Depends(get_db)):
    return material_requests.approve(db, mr_id, approved_by_person_id)


@router.post("/{mr_id}/reject", response_model=MaterialRequestRead)
def reject_material_request(
    mr_id: str,
    approved_by_person_id: str = Query(),
    reason: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return material_requests.reject(db, mr_id, approved_by_person_id, reason)


@router.post("/{mr_id}/cancel", response_model=MaterialRequestRead)
def cancel_material_request(mr_id: str, db: Session = Depends(get_db)):
    return material_requests.cancel(db, mr_id)


# ── Item management ─────────────────────────────────────────────


@router.post("/{mr_id}/items", response_model=MaterialRequestItemRead, status_code=status.HTTP_201_CREATED)
def add_item(mr_id: str, payload: MaterialRequestItemCreate, db: Session = Depends(get_db)):
    return material_requests.add_item(db, mr_id, payload)


@router.delete("/{mr_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_item(mr_id: str, item_id: str, db: Session = Depends(get_db)):
    material_requests.remove_item(db, mr_id, item_id)
