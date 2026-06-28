"""Schemas for field/vendor fiber-change-request submission."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fiber_change_request import FiberChangeRequestOperation, FiberChangeRequestStatus


class FiberChangeRequestCreate(BaseModel):
    asset_type: str = Field(min_length=1, max_length=80)
    asset_id: UUID | None = None
    operation: FiberChangeRequestOperation
    payload: dict


class FiberChangeRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_type: str
    asset_id: UUID | None
    operation: FiberChangeRequestOperation
    payload: dict
    status: FiberChangeRequestStatus
    requested_by_person_id: UUID | None
    requested_by_vendor_id: UUID | None
    reviewed_by_person_id: UUID | None
    review_notes: str | None
    reviewed_at: datetime | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime
