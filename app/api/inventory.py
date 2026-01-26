from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.services.response import list_response
from app.db import SessionLocal
from app.schemas.common import ListResponse
from app.schemas.inventory import (
    InventoryItemCreate,
    InventoryItemRead,
    InventoryItemUpdate,
    InventoryLocationCreate,
    InventoryLocationRead,
    InventoryLocationUpdate,
    InventoryStockCreate,
    InventoryStockRead,
    InventoryStockUpdate,
    ReservationCreate,
    ReservationRead,
    ReservationUpdate,
    WorkOrderMaterialCreate,
    WorkOrderMaterialRead,
    WorkOrderMaterialUpdate,
)
from app.services import inventory as inventory_service

router = APIRouter(prefix="/inventory", tags=["inventory"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/items", response_model=InventoryItemRead, status_code=status.HTTP_201_CREATED)
def create_item(payload: InventoryItemCreate, db: Session = Depends(get_db)):
    return inventory_service.inventory_items.create(db, payload)


@router.get("/items/{item_id}", response_model=InventoryItemRead)
def get_item(item_id: str, db: Session = Depends(get_db)):
    return inventory_service.inventory_items.get(db, item_id)


@router.get("/items", response_model=ListResponse[InventoryItemRead])
def list_items(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = inventory_service.inventory_items.list(
        db, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/items/{item_id}", response_model=InventoryItemRead)
def update_item(item_id: str, payload: InventoryItemUpdate, db: Session = Depends(get_db)):
    return inventory_service.inventory_items.update(db, item_id, payload)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: str, db: Session = Depends(get_db)):
    inventory_service.inventory_items.delete(db, item_id)


@router.post(
    "/locations",
    response_model=InventoryLocationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_location(payload: InventoryLocationCreate, db: Session = Depends(get_db)):
    return inventory_service.inventory_locations.create(db, payload)


@router.get("/locations/{location_id}", response_model=InventoryLocationRead)
def get_location(location_id: str, db: Session = Depends(get_db)):
    return inventory_service.inventory_locations.get(db, location_id)


@router.get("/locations", response_model=ListResponse[InventoryLocationRead])
def list_locations(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = inventory_service.inventory_locations.list(
        db, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/locations/{location_id}", response_model=InventoryLocationRead)
def update_location(
    location_id: str, payload: InventoryLocationUpdate, db: Session = Depends(get_db)
):
    return inventory_service.inventory_locations.update(db, location_id, payload)


@router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(location_id: str, db: Session = Depends(get_db)):
    inventory_service.inventory_locations.delete(db, location_id)


@router.post("/stock", response_model=InventoryStockRead, status_code=status.HTTP_201_CREATED)
def create_stock(payload: InventoryStockCreate, db: Session = Depends(get_db)):
    return inventory_service.inventory_stocks.create(db, payload)


@router.get("/stock/{stock_id}", response_model=InventoryStockRead)
def get_stock(stock_id: str, db: Session = Depends(get_db)):
    return inventory_service.inventory_stocks.get(db, stock_id)


@router.get("/stock", response_model=ListResponse[InventoryStockRead])
def list_stock(
    item_id: str | None = None,
    location_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = inventory_service.inventory_stocks.list(
        db, item_id, location_id, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/stock/{stock_id}", response_model=InventoryStockRead)
def update_stock(
    stock_id: str, payload: InventoryStockUpdate, db: Session = Depends(get_db)
):
    return inventory_service.inventory_stocks.update(db, stock_id, payload)


@router.delete("/stock/{stock_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stock(stock_id: str, db: Session = Depends(get_db)):
    inventory_service.inventory_stocks.delete(db, stock_id)


@router.post(
    "/reservations",
    response_model=ReservationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_reservation(payload: ReservationCreate, db: Session = Depends(get_db)):
    return inventory_service.reservations.create(db, payload)


@router.get("/reservations/{reservation_id}", response_model=ReservationRead)
def get_reservation(reservation_id: str, db: Session = Depends(get_db)):
    return inventory_service.reservations.get(db, reservation_id)


@router.get("/reservations", response_model=ListResponse[ReservationRead])
def list_reservations(
    item_id: str | None = None,
    work_order_id: str | None = None,
    status: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = inventory_service.reservations.list(
        db, item_id, work_order_id, status, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/reservations/{reservation_id}", response_model=ReservationRead)
def update_reservation(
    reservation_id: str, payload: ReservationUpdate, db: Session = Depends(get_db)
):
    return inventory_service.reservations.update(db, reservation_id, payload)


@router.post(
    "/reservations/{reservation_id}/release",
    response_model=ReservationRead,
)
def release_reservation(reservation_id: str, db: Session = Depends(get_db)):
    return inventory_service.release_reservation(db, reservation_id)


@router.post(
    "/reservations/{reservation_id}/consume",
    response_model=ReservationRead,
)
def consume_reservation(reservation_id: str, db: Session = Depends(get_db)):
    return inventory_service.consume_reservation(db, reservation_id)


@router.post(
    "/work-order-materials",
    response_model=WorkOrderMaterialRead,
    status_code=status.HTTP_201_CREATED,
)
def create_work_order_material(
    payload: WorkOrderMaterialCreate, db: Session = Depends(get_db)
):
    return inventory_service.work_order_materials.create(db, payload)


@router.get(
    "/work-order-materials/{material_id}",
    response_model=WorkOrderMaterialRead,
)
def get_work_order_material(material_id: str, db: Session = Depends(get_db)):
    return inventory_service.work_order_materials.get(db, material_id)


@router.get("/work-order-materials", response_model=ListResponse[WorkOrderMaterialRead])
def list_work_order_materials(
    work_order_id: str | None = None,
    status: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = inventory_service.work_order_materials.list(
        db, work_order_id, status, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch(
    "/work-order-materials/{material_id}",
    response_model=WorkOrderMaterialRead,
)
def update_work_order_material(
    material_id: str, payload: WorkOrderMaterialUpdate, db: Session = Depends(get_db)
):
    return inventory_service.work_order_materials.update(db, material_id, payload)
