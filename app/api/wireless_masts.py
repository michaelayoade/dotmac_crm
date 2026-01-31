from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.wireless_mast import (
    WirelessMastCreate,
    WirelessMastRead,
    WirelessMastUpdate,
)
from app.services import wireless_mast as wireless_mast_service

router = APIRouter(prefix="/wireless-masts")


@router.post(
    "",
    response_model=WirelessMastRead,
    status_code=status.HTTP_201_CREATED,
    tags=["wireless-masts"],
)
def create_wireless_mast(
    payload: WirelessMastCreate, db: Session = Depends(get_db)
):
    return wireless_mast_service.wireless_masts.create(db, payload)


@router.get(
    "/{mast_id}",
    response_model=WirelessMastRead,
    tags=["wireless-masts"],
)
def get_wireless_mast(mast_id: str, db: Session = Depends(get_db)):
    return wireless_mast_service.wireless_masts.get(db, mast_id)


@router.get(
    "",
    response_model=ListResponse[WirelessMastRead],
    tags=["wireless-masts"],
)
def list_wireless_masts(
    is_active: bool | None = None,
    min_latitude: float | None = None,
    min_longitude: float | None = None,
    max_latitude: float | None = None,
    max_longitude: float | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return wireless_mast_service.wireless_masts.list_response(
        db,
        is_active,
        min_latitude,
        min_longitude,
        max_latitude,
        max_longitude,
        order_by,
        order_dir,
        limit,
        offset,
    )


@router.patch(
    "/{mast_id}",
    response_model=WirelessMastRead,
    tags=["wireless-masts"],
)
def update_wireless_mast(
    mast_id: str,
    payload: WirelessMastUpdate,
    db: Session = Depends(get_db),
):
    return wireless_mast_service.wireless_masts.update(db, mast_id, payload)


@router.delete(
    "/{mast_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["wireless-masts"],
)
def delete_wireless_mast(mast_id: str, db: Session = Depends(get_db)):
    wireless_mast_service.wireless_masts.delete(db, mast_id)
