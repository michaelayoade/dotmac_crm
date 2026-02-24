from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.inventory import (
    InventoryItem,
    InventoryLocation,
    InventoryStock,
    MaterialStatus,
    Reservation,
    ReservationStatus,
    WorkOrderMaterial,
)
from app.models.workforce import WorkOrder
from app.schemas.inventory import (
    InventoryItemCreate,
    InventoryItemUpdate,
    InventoryLocationCreate,
    InventoryLocationUpdate,
    InventoryStockCreate,
    InventoryStockUpdate,
    ReservationCreate,
    ReservationUpdate,
    WorkOrderMaterialCreate,
    WorkOrderMaterialUpdate,
)
from app.services import settings_spec
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.numbering import generate_number
from app.services.response import ListResponseMixin


def _ensure_item(db: Session, item_id: str):
    if not db.get(InventoryItem, coerce_uuid(item_id)):
        raise HTTPException(status_code=404, detail="Inventory item not found")


def _ensure_location(db: Session, location_id: str):
    if not db.get(InventoryLocation, coerce_uuid(location_id)):
        raise HTTPException(status_code=404, detail="Inventory location not found")


def _ensure_work_order(db: Session, work_order_id: str):
    if not db.get(WorkOrder, coerce_uuid(work_order_id)):
        raise HTTPException(status_code=404, detail="Work order not found")


class InventoryItems(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: InventoryItemCreate):
        data = payload.model_dump()
        if not (data.get("sku") or "").strip():
            sku = generate_number(
                db=db,
                domain=SettingDomain.numbering,
                sequence_key="inventory_item_number",
                enabled_key="inventory_item_number_enabled",
                prefix_key="inventory_item_number_prefix",
                padding_key="inventory_item_number_padding",
                start_key="inventory_item_number_start",
            )
            if sku:
                data["sku"] = sku
        item = InventoryItem(**data)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def get(db: Session, item_id: str):
        item = db.get(InventoryItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        return item

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        search: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(InventoryItem)
        if is_active is None:
            query = query.filter(InventoryItem.is_active.is_(True))
        else:
            query = query.filter(InventoryItem.is_active == is_active)
        if search:
            normalized = search.strip()
            if normalized:
                pattern = f"%{normalized}%"
                query = query.filter(
                    or_(
                        InventoryItem.name.ilike(pattern),
                        InventoryItem.sku.ilike(pattern),
                        InventoryItem.description.ilike(pattern),
                    )
                )
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": InventoryItem.created_at,
                "name": InventoryItem.name,
                "sku": InventoryItem.sku,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, item_id: str, payload: InventoryItemUpdate):
        item = db.get(InventoryItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def delete(db: Session, item_id: str):
        item = db.get(InventoryItem, item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        item.is_active = False
        db.commit()


class InventoryLocations(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: InventoryLocationCreate):
        location = InventoryLocation(**payload.model_dump())
        db.add(location)
        db.commit()
        db.refresh(location)
        return location

    @staticmethod
    def get(db: Session, location_id: str):
        location = db.get(InventoryLocation, location_id)
        if not location:
            raise HTTPException(status_code=404, detail="Inventory location not found")
        return location

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(InventoryLocation)
        if is_active is None:
            query = query.filter(InventoryLocation.is_active.is_(True))
        else:
            query = query.filter(InventoryLocation.is_active == is_active)
        query = apply_ordering(query, order_by, order_dir, {"created_at": InventoryLocation.created_at})
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, location_id: str, payload: InventoryLocationUpdate):
        location = db.get(InventoryLocation, location_id)
        if not location:
            raise HTTPException(status_code=404, detail="Inventory location not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(location, key, value)
        db.commit()
        db.refresh(location)
        return location

    @staticmethod
    def delete(db: Session, location_id: str):
        location = db.get(InventoryLocation, location_id)
        if not location:
            raise HTTPException(status_code=404, detail="Inventory location not found")
        location.is_active = False
        db.commit()


class InventoryStocks(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: InventoryStockCreate):
        _ensure_item(db, str(payload.item_id))
        _ensure_location(db, str(payload.location_id))
        stock = InventoryStock(**payload.model_dump())
        db.add(stock)
        db.commit()
        db.refresh(stock)
        return stock

    @staticmethod
    def get(db: Session, stock_id: str):
        stock = db.get(InventoryStock, stock_id)
        if not stock:
            raise HTTPException(status_code=404, detail="Inventory stock not found")
        return stock

    @staticmethod
    def list(
        db: Session,
        item_id: str | None,
        location_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(InventoryStock)
        if item_id:
            query = query.filter(InventoryStock.item_id == item_id)
        if location_id:
            query = query.filter(InventoryStock.location_id == location_id)
        if is_active is None:
            query = query.filter(InventoryStock.is_active.is_(True))
        else:
            query = query.filter(InventoryStock.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": InventoryStock.created_at,
                "updated_at": InventoryStock.updated_at,
                "quantity_on_hand": InventoryStock.quantity_on_hand,
                "reserved_quantity": InventoryStock.reserved_quantity,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, stock_id: str, payload: InventoryStockUpdate):
        stock = db.get(InventoryStock, stock_id)
        if not stock:
            raise HTTPException(status_code=404, detail="Inventory stock not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("item_id"):
            _ensure_item(db, str(data["item_id"]))
        if data.get("location_id"):
            _ensure_location(db, str(data["location_id"]))
        for key, value in data.items():
            setattr(stock, key, value)
        db.commit()
        db.refresh(stock)
        return stock

    @staticmethod
    def delete(db: Session, stock_id: str):
        stock = db.get(InventoryStock, stock_id)
        if not stock:
            raise HTTPException(status_code=404, detail="Inventory stock not found")
        stock.is_active = False
        db.commit()


class Reservations(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ReservationCreate):
        _ensure_item(db, str(payload.item_id))
        _ensure_location(db, str(payload.location_id))
        if payload.work_order_id:
            _ensure_work_order(db, str(payload.work_order_id))
        stock = (
            db.query(InventoryStock)
            .filter(InventoryStock.item_id == payload.item_id)
            .filter(InventoryStock.location_id == payload.location_id)
            .first()
        )
        if not stock:
            raise HTTPException(status_code=404, detail="Inventory stock not found")
        available = stock.quantity_on_hand - stock.reserved_quantity
        if available < payload.quantity:
            raise HTTPException(status_code=400, detail="Insufficient stock")
        stock.reserved_quantity += payload.quantity
        data = payload.model_dump()
        fields_set = payload.model_fields_set
        if "status" not in fields_set:
            default_status = settings_spec.resolve_value(db, SettingDomain.inventory, "default_reservation_status")
            if default_status:
                data["status"] = validate_enum(default_status, ReservationStatus, "status")
        reservation = Reservation(**data)
        db.add(reservation)
        db.commit()
        db.refresh(reservation)
        return reservation

    @staticmethod
    def get(db: Session, reservation_id: str):
        reservation = db.get(Reservation, coerce_uuid(reservation_id))
        if not reservation:
            raise HTTPException(status_code=404, detail="Reservation not found")
        return reservation

    @staticmethod
    def list(
        db: Session,
        item_id: str | None,
        work_order_id: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Reservation)
        if item_id:
            query = query.filter(Reservation.item_id == item_id)
        if work_order_id:
            query = query.filter(Reservation.work_order_id == work_order_id)
        if status:
            try:
                query = query.filter(Reservation.status == ReservationStatus(status))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid status") from exc
        query = apply_ordering(query, order_by, order_dir, {"created_at": Reservation.created_at})
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, reservation_id: str, payload: ReservationUpdate):
        reservation = db.get(Reservation, coerce_uuid(reservation_id))
        if not reservation:
            raise HTTPException(status_code=404, detail="Reservation not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(reservation, key, value)
        db.commit()
        db.refresh(reservation)
        return reservation


class WorkOrderMaterials(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: WorkOrderMaterialCreate):
        _ensure_work_order(db, str(payload.work_order_id))
        _ensure_item(db, str(payload.item_id))
        if payload.reservation_id and not db.get(Reservation, coerce_uuid(payload.reservation_id)):
            raise HTTPException(status_code=404, detail="Reservation not found")
        data = payload.model_dump()
        fields_set = payload.model_fields_set
        if "status" not in fields_set:
            default_status = settings_spec.resolve_value(db, SettingDomain.inventory, "default_material_status")
            if default_status:
                data["status"] = validate_enum(default_status, MaterialStatus, "status")
        material = WorkOrderMaterial(**data)
        db.add(material)
        db.commit()
        db.refresh(material)
        return material

    @staticmethod
    def get(db: Session, material_id: str):
        material = db.get(WorkOrderMaterial, material_id)
        if not material:
            raise HTTPException(status_code=404, detail="Work order material not found")
        return material

    @staticmethod
    def list(
        db: Session,
        work_order_id: str | None,
        status: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(WorkOrderMaterial)
        if work_order_id:
            query = query.filter(WorkOrderMaterial.work_order_id == work_order_id)
        if status:
            try:
                query = query.filter(WorkOrderMaterial.status == MaterialStatus(status))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid status") from exc
        query = apply_ordering(query, order_by, order_dir, {"created_at": WorkOrderMaterial.created_at})
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, material_id: str, payload: WorkOrderMaterialUpdate):
        material = db.get(WorkOrderMaterial, material_id)
        if not material:
            raise HTTPException(status_code=404, detail="Work order material not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("reservation_id") and not db.get(Reservation, coerce_uuid(data["reservation_id"])):
            raise HTTPException(status_code=404, detail="Reservation not found")
        for key, value in data.items():
            setattr(material, key, value)
        db.commit()
        db.refresh(material)
        return material


def release_reservation(db: Session, reservation_id: str):
    reservation = db.get(Reservation, coerce_uuid(reservation_id))
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != ReservationStatus.active:
        return reservation
    stock = (
        db.query(InventoryStock)
        .filter(InventoryStock.item_id == reservation.item_id)
        .filter(InventoryStock.location_id == reservation.location_id)
        .first()
    )
    if not stock:
        raise HTTPException(status_code=404, detail="Inventory stock not found")
    stock.reserved_quantity = max(stock.reserved_quantity - reservation.quantity, 0)
    reservation.status = ReservationStatus.released
    db.commit()
    db.refresh(reservation)
    return reservation


def consume_reservation(db: Session, reservation_id: str):
    reservation = db.get(Reservation, coerce_uuid(reservation_id))
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status != ReservationStatus.active:
        return reservation
    stock = (
        db.query(InventoryStock)
        .filter(InventoryStock.item_id == reservation.item_id)
        .filter(InventoryStock.location_id == reservation.location_id)
        .first()
    )
    if not stock:
        raise HTTPException(status_code=404, detail="Inventory stock not found")
    stock.reserved_quantity = max(stock.reserved_quantity - reservation.quantity, 0)
    stock.quantity_on_hand = max(stock.quantity_on_hand - reservation.quantity, 0)
    reservation.status = ReservationStatus.consumed
    db.commit()
    db.refresh(reservation)
    return reservation


inventory_items = InventoryItems()
inventory_locations = InventoryLocations()
inventory_stocks = InventoryStocks()
reservations = Reservations()
work_order_materials = WorkOrderMaterials()
