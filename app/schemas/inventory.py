from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.inventory import MaterialStatus, ReservationStatus


class InventoryItemBase(BaseModel):
    sku: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    unit: str | None = Field(default=None, max_length=40)
    is_active: bool = True


class InventoryItemCreate(InventoryItemBase):
    pass


class InventoryItemUpdate(BaseModel):
    sku: str | None = Field(default=None, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    unit: str | None = Field(default=None, max_length=40)
    is_active: bool | None = None


class InventoryItemRead(InventoryItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class InventoryLocationBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    code: str | None = Field(default=None, max_length=80)
    address_id: UUID | None = None
    is_active: bool = True


class InventoryLocationCreate(InventoryLocationBase):
    pass


class InventoryLocationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    code: str | None = Field(default=None, max_length=80)
    address_id: UUID | None = None
    is_active: bool | None = None


class InventoryLocationRead(InventoryLocationBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class InventoryStockBase(BaseModel):
    item_id: UUID
    location_id: UUID
    quantity_on_hand: int = Field(default=0, ge=0)
    reserved_quantity: int = Field(default=0, ge=0)
    is_active: bool = True


class InventoryStockCreate(InventoryStockBase):
    pass


class InventoryStockUpdate(BaseModel):
    item_id: UUID | None = None
    location_id: UUID | None = None
    quantity_on_hand: int | None = Field(default=None, ge=0)
    reserved_quantity: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class InventoryStockRead(InventoryStockBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ReservationBase(BaseModel):
    item_id: UUID
    location_id: UUID
    work_order_id: UUID | None = None
    quantity: int = Field(ge=1)
    status: ReservationStatus = ReservationStatus.active


class ReservationCreate(ReservationBase):
    pass


class ReservationUpdate(BaseModel):
    status: ReservationStatus | None = None


class ReservationRead(ReservationBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class WorkOrderMaterialBase(BaseModel):
    work_order_id: UUID
    item_id: UUID
    reservation_id: UUID | None = None
    quantity: int = Field(ge=1)
    status: MaterialStatus = MaterialStatus.required
    notes: str | None = None


class WorkOrderMaterialCreate(WorkOrderMaterialBase):
    pass


class WorkOrderMaterialUpdate(BaseModel):
    reservation_id: UUID | None = None
    quantity: int | None = Field(default=None, ge=1)
    status: MaterialStatus | None = None
    notes: str | None = None


class WorkOrderMaterialRead(WorkOrderMaterialBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
