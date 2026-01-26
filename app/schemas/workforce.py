from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.workforce import WorkOrderPriority, WorkOrderStatus, WorkOrderType


class WorkOrderBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: WorkOrderStatus = WorkOrderStatus.draft
    priority: WorkOrderPriority = WorkOrderPriority.normal
    work_type: WorkOrderType = WorkOrderType.install
    account_id: UUID | None = None
    subscription_id: UUID | None = None
    service_order_id: UUID | None = None
    ticket_id: UUID | None = None
    project_id: UUID | None = None
    address_id: UUID | None = None
    assigned_to_person_id: UUID | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    tags: list | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    is_active: bool = True


class WorkOrderCreate(WorkOrderBase):
    pass


class WorkOrderUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: WorkOrderStatus | None = None
    priority: WorkOrderPriority | None = None
    work_type: WorkOrderType | None = None
    account_id: UUID | None = None
    subscription_id: UUID | None = None
    service_order_id: UUID | None = None
    ticket_id: UUID | None = None
    project_id: UUID | None = None
    address_id: UUID | None = None
    assigned_to_person_id: UUID | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    tags: list | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    is_active: bool | None = None


class WorkOrderRead(WorkOrderBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class WorkOrderAssignmentBase(BaseModel):
    work_order_id: UUID
    person_id: UUID
    role: str | None = Field(default=None, max_length=60)
    is_primary: bool = False


class WorkOrderAssignmentCreate(WorkOrderAssignmentBase):
    pass


class WorkOrderAssignmentUpdate(BaseModel):
    role: str | None = Field(default=None, max_length=60)
    is_primary: bool | None = None


class WorkOrderAssignmentRead(WorkOrderAssignmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    assigned_at: datetime


class WorkOrderNoteBase(BaseModel):
    work_order_id: UUID
    author_person_id: UUID | None = None
    body: str = Field(min_length=1)
    is_internal: bool = False
    attachments: list | None = None


class WorkOrderNoteCreate(WorkOrderNoteBase):
    pass


class WorkOrderNoteUpdate(BaseModel):
    body: str | None = None
    is_internal: bool | None = None
    attachments: list | None = None


class WorkOrderNoteRead(WorkOrderNoteBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
