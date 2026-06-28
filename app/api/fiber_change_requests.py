"""Fiber-change-request submission JSON API.

Thin wrappers over app.services.fiber_change_requests for the field/vendor
submission path (the service was designed for this via requested_by_vendor_id,
but only the admin review side was wired). Approve/reject stay admin-web.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.fiber_change_request import FiberChangeRequestStatus
from app.schemas.fiber_change_request import FiberChangeRequestCreate, FiberChangeRequestRead
from app.services import fiber_change_requests as fcr_service
from app.services.common import validate_enum

router = APIRouter(prefix="/fiber-change-requests", tags=["fiber-change-requests"])


@router.post("", response_model=FiberChangeRequestRead, status_code=status.HTTP_201_CREATED)
def submit_request(payload: FiberChangeRequestCreate, db: Session = Depends(get_db), auth=Depends(get_current_user)):
    requested_by = str(auth["person_id"]) if auth and auth.get("person_id") else None
    return fcr_service.create_request(
        db,
        asset_type=payload.asset_type,
        asset_id=str(payload.asset_id) if payload.asset_id else None,
        operation=payload.operation,
        payload=payload.payload,
        requested_by_person_id=requested_by,
        requested_by_vendor_id=None,
    )


@router.get("", response_model=list[FiberChangeRequestRead])
def list_requests(status: str | None = Query(default=None), db: Session = Depends(get_db)):
    status_enum = validate_enum(status, FiberChangeRequestStatus, "status") if status else None
    return fcr_service.list_requests(db, status_enum)


@router.get("/{request_id}", response_model=FiberChangeRequestRead)
def get_request(request_id: str, db: Session = Depends(get_db)):
    return fcr_service.get_request(db, request_id)
