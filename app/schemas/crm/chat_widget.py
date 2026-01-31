"""Pydantic schemas for chat widget API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Widget Configuration Schemas
# --------------------------------------------------------------------------


class PrechatField(BaseModel):
    """Definition for a pre-chat form field."""

    name: str = Field(..., max_length=60)
    label: str = Field(..., max_length=120)
    field_type: Literal["text", "email", "phone", "textarea", "select"] = "text"
    required: bool = False
    placeholder: str | None = Field(default=None, max_length=120)
    options: list[str] | None = None  # For select fields


class BusinessHoursDay(BaseModel):
    """Business hours for a single day."""

    enabled: bool = True
    start: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    end: str = Field(default="17:00", pattern=r"^\d{2}:\d{2}$")


class BusinessHours(BaseModel):
    """Weekly business hours configuration."""

    timezone: str = "UTC"
    monday: BusinessHoursDay = Field(default_factory=BusinessHoursDay)
    tuesday: BusinessHoursDay = Field(default_factory=BusinessHoursDay)
    wednesday: BusinessHoursDay = Field(default_factory=BusinessHoursDay)
    thursday: BusinessHoursDay = Field(default_factory=BusinessHoursDay)
    friday: BusinessHoursDay = Field(default_factory=BusinessHoursDay)
    saturday: BusinessHoursDay = Field(default_factory=lambda: BusinessHoursDay(enabled=False))
    sunday: BusinessHoursDay = Field(default_factory=lambda: BusinessHoursDay(enabled=False))


class ChatWidgetConfigCreate(BaseModel):
    """Payload for creating a widget configuration."""

    name: str = Field(..., min_length=1, max_length=160)
    allowed_domains: list[str] = Field(default_factory=list)
    primary_color: str = Field(default="#3B82F6", max_length=20)
    bubble_position: Literal["bottom-right", "bottom-left"] = "bottom-right"
    welcome_message: str | None = Field(default=None, max_length=500)
    placeholder_text: str = Field(default="Type a message...", max_length=120)
    widget_title: str = Field(default="Chat with us", max_length=80)
    offline_message: str | None = Field(default=None, max_length=500)
    prechat_form_enabled: bool = False
    prechat_fields: list[PrechatField] | None = None
    business_hours: BusinessHours | None = None
    rate_limit_messages_per_minute: int = Field(default=10, ge=1, le=60)
    rate_limit_sessions_per_ip: int = Field(default=5, ge=1, le=20)
    connector_config_id: UUID | None = None


class ChatWidgetConfigUpdate(BaseModel):
    """Payload for updating a widget configuration."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    allowed_domains: list[str] | None = None
    primary_color: str | None = Field(default=None, max_length=20)
    bubble_position: Literal["bottom-right", "bottom-left"] | None = None
    welcome_message: str | None = Field(default=None, max_length=500)
    placeholder_text: str | None = Field(default=None, max_length=120)
    widget_title: str | None = Field(default=None, max_length=80)
    offline_message: str | None = None
    prechat_form_enabled: bool | None = None
    prechat_fields: list[PrechatField] | None = None
    business_hours: BusinessHours | None = None
    rate_limit_messages_per_minute: int | None = Field(default=None, ge=1, le=60)
    rate_limit_sessions_per_ip: int | None = Field(default=None, ge=1, le=20)
    is_active: bool | None = None
    connector_config_id: UUID | None = None


class ChatWidgetConfigRead(BaseModel):
    """Full widget configuration for admin."""

    id: UUID
    name: str
    allowed_domains: list[str]
    primary_color: str
    bubble_position: str
    welcome_message: str | None
    placeholder_text: str
    widget_title: str
    offline_message: str | None
    prechat_form_enabled: bool
    prechat_fields: list[PrechatField] | None
    business_hours: BusinessHours | None
    rate_limit_messages_per_minute: int
    rate_limit_sessions_per_ip: int
    is_active: bool
    connector_config_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChatWidgetPublicConfig(BaseModel):
    """Public-facing widget configuration (no sensitive data)."""

    widget_id: UUID
    primary_color: str
    bubble_position: str
    welcome_message: str | None
    placeholder_text: str
    widget_title: str
    offline_message: str | None
    prechat_form_enabled: bool
    prechat_fields: list[PrechatField] | None
    is_online: bool = True  # Computed from business_hours


# --------------------------------------------------------------------------
# Widget Session Schemas
# --------------------------------------------------------------------------


class WidgetSessionCreate(BaseModel):
    """Payload for creating a visitor session."""

    fingerprint: str | None = Field(default=None, max_length=64)
    page_url: str | None = Field(default=None, max_length=2048)
    referrer_url: str | None = Field(default=None, max_length=2048)


class WidgetSessionRead(BaseModel):
    """Response when creating/getting a session."""

    session_id: UUID
    visitor_token: str
    conversation_id: UUID | None
    is_identified: bool
    identified_name: str | None

    model_config = {"from_attributes": True}


class WidgetIdentifyRequest(BaseModel):
    """Payload for identifying an anonymous visitor."""

    email: str = Field(..., min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=160)
    custom_fields: dict | None = None


class WidgetPrechatSubmit(BaseModel):
    """Payload for submitting pre-chat form."""

    fields: dict[str, str | None] = Field(default_factory=dict)


class WidgetIdentifyResponse(BaseModel):
    """Response after visitor identification."""

    session_id: UUID
    person_id: UUID
    email: str
    name: str | None


# --------------------------------------------------------------------------
# Widget Message Schemas
# --------------------------------------------------------------------------


class WidgetMessageSend(BaseModel):
    """Payload for sending a message from the widget."""

    body: str = Field(..., min_length=1, max_length=5000)


class WidgetMessageRead(BaseModel):
    """Message as returned to the widget."""

    id: UUID
    body: str
    direction: Literal["inbound", "outbound"]
    created_at: datetime
    author_name: str | None = None
    author_avatar: str | None = None

    model_config = {"from_attributes": True}


class WidgetMessagesResponse(BaseModel):
    """Response for message list endpoint."""

    messages: list[WidgetMessageRead]
    has_more: bool = False


class WidgetMessageResponse(BaseModel):
    """Response when sending a message."""

    message_id: UUID
    conversation_id: UUID
    status: str


# --------------------------------------------------------------------------
# Widget Embed Code
# --------------------------------------------------------------------------


class WidgetEmbedCode(BaseModel):
    """Embed code for adding widget to a website."""

    script_url: str
    config_id: UUID
    embed_html: str
