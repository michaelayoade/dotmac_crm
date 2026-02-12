from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.material_request import MaterialRequestPriority, MaterialRequestStatus


class MaterialRequestItemBase(BaseModel):
    item_id: UUID
    quantity: int = Field(ge=1)
    notes: str | None = None


class MaterialRequestItemCreate(MaterialRequestItemBase):
    pass


class MaterialRequestItemRead(MaterialRequestItemBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    material_request_id: UUID
    created_at: datetime


class MaterialRequestBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ticket_id: UUID | None = None
    project_id: UUID | None = None
    work_order_id: UUID | None = None
    requested_by_person_id: UUID
    priority: MaterialRequestPriority = MaterialRequestPriority.medium
    notes: str | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class MaterialRequestCreate(MaterialRequestBase):
    items: list[MaterialRequestItemCreate] | None = None


class MaterialRequestUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ticket_id: UUID | None = None
    project_id: UUID | None = None
    priority: MaterialRequestPriority | None = None
    notes: str | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class MaterialRequestRead(MaterialRequestBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    status: MaterialRequestStatus
    approved_by_person_id: UUID | None = None
    erp_material_request_id: str | None = None
    number: str | None = None
    is_active: bool
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    fulfilled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    items: list[MaterialRequestItemRead] = []
