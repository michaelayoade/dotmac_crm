from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.inventory import InventoryItemRead, InventoryLocationRead
from app.services import inventory as inventory_service
from app.services.response import list_response

router = APIRouter(prefix="/inventory", tags=["field-inventory"])


@router.get("/items", response_model=ListResponse[InventoryItemRead])
def list_field_inventory_items(
    q: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = inventory_service.inventory_items.list(
        db,
        is_active=True,
        search=q or search,
        order_by="name",
        order_dir="asc",
        limit=limit,
        offset=offset,
    )
    return list_response(items, limit, offset)


@router.get("/locations", response_model=ListResponse[InventoryLocationRead])
def list_field_inventory_locations(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = inventory_service.inventory_locations.list(
        db,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=limit,
        offset=offset,
    )
    return list_response(items, limit, offset)
