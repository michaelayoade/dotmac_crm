from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.crm.enums import ChannelType, ConversationPriority


class AiIntakeDepartmentMapping(BaseModel):
    key: str = Field(..., min_length=1, max_length=60)
    label: str = Field(..., min_length=1, max_length=120)
    team_id: UUID | None = None
    tags: list[str] | None = None
    priority: ConversationPriority | None = None
    notify_email: str | None = Field(default=None, max_length=255)


class AiIntakeConfigBase(BaseModel):
    scope_key: str = Field(..., min_length=1, max_length=160)
    channel_type: ChannelType
    is_enabled: bool = False
    confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    allow_followup_questions: bool = True
    max_clarification_turns: int = Field(default=1, ge=0, le=5)
    escalate_after_minutes: int = Field(default=5, ge=0, le=1440)
    exclude_campaign_attribution: bool = True
    fallback_team_id: UUID | None = None
    instructions: str | None = None
    department_mappings: list[AiIntakeDepartmentMapping] = Field(default_factory=list)


class AiIntakeConfigCreate(AiIntakeConfigBase):
    pass


class AiIntakeConfigUpdate(BaseModel):
    is_enabled: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    allow_followup_questions: bool | None = None
    max_clarification_turns: int | None = Field(default=None, ge=0, le=5)
    escalate_after_minutes: int | None = Field(default=None, ge=0, le=1440)
    exclude_campaign_attribution: bool | None = None
    fallback_team_id: UUID | None = None
    instructions: str | None = None
    department_mappings: list[AiIntakeDepartmentMapping] | None = None


class AiIntakeConfigRead(AiIntakeConfigBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
