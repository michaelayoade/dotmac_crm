from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.crm.enums import CampaignRecipientStatus, CampaignStatus, CampaignType


class SegmentFilter(BaseModel):
    """Validates the JSON audience filter for campaign targeting."""

    party_status: list[str] | None = None
    active_status: str | None = None
    organization_ids: list[UUID] | None = None
    regions: list[str] | None = None
    tags: list[str] | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


class CampaignBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    campaign_type: CampaignType = CampaignType.one_time
    subject: str | None = Field(default=None, max_length=200)
    body_html: str | None = None
    body_text: str | None = None
    campaign_sender_id: UUID | None = None
    campaign_smtp_config_id: UUID | None = None
    from_name: str | None = Field(default=None, max_length=160)
    from_email: str | None = Field(default=None, max_length=255)
    reply_to: str | None = Field(default=None, max_length=255)
    segment_filter: dict | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class CampaignCreate(CampaignBase):
    pass


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    campaign_type: CampaignType | None = None
    subject: str | None = Field(default=None, max_length=200)
    body_html: str | None = None
    body_text: str | None = None
    campaign_sender_id: UUID | None = None
    campaign_smtp_config_id: UUID | None = None
    from_name: str | None = Field(default=None, max_length=160)
    from_email: str | None = Field(default=None, max_length=255)
    reply_to: str | None = Field(default=None, max_length=255)
    segment_filter: dict | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")
    is_active: bool | None = None


class CampaignRead(CampaignBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    status: CampaignStatus
    scheduled_at: datetime | None = None
    sending_started_at: datetime | None = None
    completed_at: datetime | None = None
    total_recipients: int = 0
    sent_count: int = 0
    delivered_count: int = 0
    failed_count: int = 0
    opened_count: int = 0
    clicked_count: int = 0
    created_by_id: UUID | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class CampaignStepBase(BaseModel):
    campaign_id: UUID
    step_index: int = 0
    name: str | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, max_length=200)
    body_html: str | None = None
    body_text: str | None = None
    delay_days: int = Field(default=0, ge=0)


class CampaignStepCreate(CampaignStepBase):
    pass


class CampaignStepUpdate(BaseModel):
    step_index: int | None = None
    name: str | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, max_length=200)
    body_html: str | None = None
    body_text: str | None = None
    delay_days: int | None = Field(default=None, ge=0)


class CampaignStepRead(CampaignStepBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class CampaignRecipientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    campaign_id: UUID
    person_id: UUID
    step_id: UUID | None = None
    email: str
    status: CampaignRecipientStatus
    notification_id: UUID | None = None
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    failed_reason: str | None = None
    created_at: datetime
