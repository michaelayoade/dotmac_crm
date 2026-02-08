from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus


class ConversationBase(BaseModel):
    person_id: UUID
    ticket_id: UUID | None = None
    status: ConversationStatus = ConversationStatus.open
    subject: str | None = Field(default=None, max_length=200)
    last_message_at: datetime | None = None
    is_active: bool = True
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ConversationCreate(ConversationBase):
    pass


class ConversationUpdate(BaseModel):
    person_id: UUID | None = None
    ticket_id: UUID | None = None
    status: ConversationStatus | None = None
    subject: str | None = Field(default=None, max_length=200)
    last_message_at: datetime | None = None
    is_active: bool | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class ConversationRead(ConversationBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ConversationAssignmentBase(BaseModel):
    conversation_id: UUID
    team_id: UUID | None = None
    agent_id: UUID | None = None
    assigned_by_id: UUID | None = None
    assigned_at: datetime | None = None
    is_active: bool = True


class ConversationAssignmentCreate(ConversationAssignmentBase):
    pass


class ConversationAssignmentUpdate(BaseModel):
    team_id: UUID | None = None
    agent_id: UUID | None = None
    assigned_by_id: UUID | None = None
    assigned_at: datetime | None = None
    is_active: bool | None = None


class ConversationAssignmentRead(ConversationAssignmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class ConversationTagBase(BaseModel):
    conversation_id: UUID
    tag: str = Field(min_length=1, max_length=80)


class ConversationTagCreate(ConversationTagBase):
    pass


class ConversationTagRead(ConversationTagBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class MessageBase(BaseModel):
    conversation_id: UUID
    person_channel_id: UUID | None = None
    channel_target_id: UUID | None = None
    reply_to_message_id: UUID | None = None
    channel_type: ChannelType
    direction: MessageDirection
    status: MessageStatus = MessageStatus.received
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = None
    external_id: str | None = Field(default=None, max_length=120)
    external_ref: str | None = Field(default=None, max_length=255)
    author_id: UUID | None = None
    sent_at: datetime | None = None
    received_at: datetime | None = None
    read_at: datetime | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class MessageCreate(MessageBase):
    pass


class MessageUpdate(BaseModel):
    status: MessageStatus | None = None
    body: str | None = None
    sent_at: datetime | None = None
    received_at: datetime | None = None
    read_at: datetime | None = None
    reply_to_message_id: UUID | None = None
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class MessageRead(MessageBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class MessageAttachmentBase(BaseModel):
    message_id: UUID
    file_name: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=120)
    file_size: int | None = None
    external_url: str | None = Field(default=None, max_length=500)
    metadata_: dict | None = Field(default=None, serialization_alias="metadata")


class MessageAttachmentCreate(MessageAttachmentBase):
    pass


class MessageAttachmentRead(MessageAttachmentBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    created_at: datetime
