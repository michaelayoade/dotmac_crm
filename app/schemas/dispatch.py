from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.dispatch import DispatchQueueStatus


class SkillBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    is_active: bool = True


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    is_active: bool | None = None


class SkillRead(SkillBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class TechnicianProfileBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    person_id: UUID
    title: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    is_active: bool = True


class TechnicianProfileCreate(TechnicianProfileBase):
    pass


class TechnicianProfileUpdate(BaseModel):
    person_id: UUID | None = None
    title: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    is_active: bool | None = None


class TechnicianProfileRead(TechnicianProfileBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class TechnicianSkillBase(BaseModel):
    technician_id: UUID
    skill_id: UUID
    proficiency: int | None = Field(default=None, ge=1, le=5)
    is_primary: bool = False
    is_active: bool = True


class TechnicianSkillCreate(TechnicianSkillBase):
    pass


class TechnicianSkillUpdate(BaseModel):
    technician_id: UUID | None = None
    skill_id: UUID | None = None
    proficiency: int | None = Field(default=None, ge=1, le=5)
    is_primary: bool | None = None
    is_active: bool | None = None


class TechnicianSkillRead(TechnicianSkillBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class ShiftBase(BaseModel):
    technician_id: UUID
    start_at: datetime
    end_at: datetime
    timezone: str | None = Field(default=None, max_length=64)
    is_active: bool = True


class ShiftCreate(ShiftBase):
    pass


class ShiftUpdate(BaseModel):
    technician_id: UUID | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class ShiftRead(ShiftBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class AvailabilityBlockBase(BaseModel):
    technician_id: UUID
    start_at: datetime
    end_at: datetime
    reason: str | None = Field(default=None, max_length=160)
    is_available: bool = False
    is_active: bool = True


class AvailabilityBlockCreate(AvailabilityBlockBase):
    pass


class AvailabilityBlockUpdate(BaseModel):
    technician_id: UUID | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=160)
    is_available: bool | None = None
    is_active: bool | None = None


class AvailabilityBlockRead(AvailabilityBlockBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class DispatchRuleBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=0, ge=0)
    work_type: str | None = Field(default=None, max_length=40)
    work_priority: str | None = Field(default=None, max_length=40)
    region: str | None = Field(default=None, max_length=120)
    skill_ids: list[UUID] | None = None
    auto_assign: bool = False
    is_active: bool = True


class DispatchRuleCreate(DispatchRuleBase):
    pass


class DispatchRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    priority: int | None = Field(default=None, ge=0)
    work_type: str | None = Field(default=None, max_length=40)
    work_priority: str | None = Field(default=None, max_length=40)
    region: str | None = Field(default=None, max_length=120)
    skill_ids: list[UUID] | None = None
    auto_assign: bool | None = None
    is_active: bool | None = None


class DispatchRuleRead(DispatchRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class WorkOrderAssignmentQueueBase(BaseModel):
    work_order_id: UUID
    status: DispatchQueueStatus = DispatchQueueStatus.queued
    reason: str | None = None


class WorkOrderAssignmentQueueCreate(WorkOrderAssignmentQueueBase):
    pass


class WorkOrderAssignmentQueueUpdate(BaseModel):
    status: DispatchQueueStatus | None = None
    reason: str | None = None


class WorkOrderAssignmentQueueRead(WorkOrderAssignmentQueueBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class AutoAssignResponse(BaseModel):
    work_order_id: UUID
    technician_id: UUID | None = None
    assignment_status: DispatchQueueStatus
    detail: str | None = None
