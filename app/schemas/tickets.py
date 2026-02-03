from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.tickets import TicketChannel, TicketPriority, TicketStatus


class TicketBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    subscriber_id: UUID | None = None
    lead_id: UUID | None = None
    customer_person_id: UUID | None = None
    created_by_person_id: UUID | None = None
    assigned_to_person_id: UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: TicketStatus = TicketStatus.new
    priority: TicketPriority = TicketPriority.normal
    ticket_type: str | None = Field(default=None, max_length=120)
    channel: TicketChannel = TicketChannel.web
    tags: list[str] | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    due_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    is_active: bool = True


class TicketCreate(TicketBase):
    pass


class TicketUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    subscriber_id: UUID | None = None
    lead_id: UUID | None = None
    customer_person_id: UUID | None = None
    created_by_person_id: UUID | None = None
    assigned_to_person_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    ticket_type: str | None = Field(default=None, max_length=120)
    channel: TicketChannel | None = None
    tags: list[str] | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )
    due_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _validate_status_timestamps(self) -> "TicketUpdate":
        fields_set = self.model_fields_set
        if "status" in fields_set:
            if self.status == TicketStatus.resolved and "resolved_at" not in fields_set:
                raise ValueError("resolved_at is required when status is resolved")
            if self.status == TicketStatus.closed and "closed_at" not in fields_set:
                raise ValueError("closed_at is required when status is closed")
        return self


class TicketRead(TicketBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class TicketBulkUpdateRequest(BaseModel):
    ticket_ids: list[UUID]
    update: TicketUpdate


class TicketBulkUpdateResponse(BaseModel):
    updated: int


class TicketCommentBulkCreateRequest(BaseModel):
    ticket_ids: list[UUID]
    author_person_id: UUID | None = None
    body: str = Field(min_length=1)
    is_internal: bool = False
    attachments: list[dict] | None = None


class TicketCommentBulkCreateResponse(BaseModel):
    created: int
    comment_ids: list[UUID]


class TicketCommentBase(BaseModel):
    ticket_id: UUID
    author_person_id: UUID | None = None
    body: str = Field(min_length=1)
    is_internal: bool = False
    attachments: list[dict] | None = None


class TicketCommentCreate(TicketCommentBase):
    pass


class TicketCommentUpdate(BaseModel):
    body: str | None = None
    is_internal: bool | None = None
    attachments: list[dict] | None = None


class TicketCommentRead(TicketCommentBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class TicketSlaEventBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ticket_id: UUID
    event_type: str = Field(min_length=1, max_length=60)
    expected_at: datetime | None = None
    actual_at: datetime | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )


class TicketSlaEventCreate(TicketSlaEventBase):
    pass


class TicketSlaEventUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    event_type: str | None = Field(default=None, min_length=1, max_length=60)
    expected_at: datetime | None = None
    actual_at: datetime | None = None
    metadata_: dict | None = Field(
        default=None,
        serialization_alias="metadata",
    )


class TicketSlaEventRead(TicketSlaEventBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
