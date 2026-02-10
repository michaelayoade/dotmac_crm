from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.crm.enums import ChannelType


class MessageTemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    channel_type: ChannelType
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1)
    is_active: bool = True
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class MessageTemplateCreate(MessageTemplateBase):
    pass


class MessageTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    channel_type: ChannelType | None = None
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class MessageTemplateRead(MessageTemplateBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
