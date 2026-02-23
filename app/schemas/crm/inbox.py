from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.models.crm.enums import ChannelType

MAX_CC_ADDRESSES = 20


class InboxSendRequest(BaseModel):
    conversation_id: UUID
    channel_type: ChannelType
    channel_target_id: UUID | None = None
    person_channel_id: UUID | None = None
    reply_to_message_id: UUID | None = None
    template_id: UUID | None = None
    whatsapp_template_name: str | None = None
    whatsapp_template_language: str | None = None
    whatsapp_template_components: list[dict] | None = None
    subject: str | None = Field(default=None, max_length=200)
    cc_addresses: list[EmailStr] | None = Field(default=None, max_length=MAX_CC_ADDRESSES)
    body: str | None = None
    scheduled_at: datetime | None = None
    personalization: dict | None = None
    attachments: list[dict] | None = None

    @model_validator(mode="after")
    def _require_body_or_attachments(self):
        body_text = (self.body or "").strip()
        has_attachments = bool(self.attachments)
        if self.channel_type == ChannelType.whatsapp and self.whatsapp_template_name:
            if not self.whatsapp_template_language:
                raise ValueError("WhatsApp template language is required.")
            self.body = body_text
            return self
        if not body_text and not has_attachments and not self.template_id:
            raise ValueError("Message body is required when no attachments are provided.")
        self.body = body_text
        return self


class InboxSendResponse(BaseModel):
    message_id: UUID
    status: str


class WhatsAppWebhookPayload(BaseModel):
    contact_address: str = Field(min_length=1, max_length=255)
    contact_name: str | None = Field(default=None, max_length=160)
    message_id: str | None = Field(default=None, max_length=255)
    channel_target_id: UUID | None = None
    body: str = Field(min_length=1)
    received_at: datetime | None = None
    metadata: dict | None = None


class EmailWebhookPayload(BaseModel):
    contact_address: str = Field(min_length=1, max_length=255)
    contact_name: str | None = Field(default=None, max_length=160)
    message_id: str | None = Field(default=None, max_length=255)
    channel_target_id: UUID | None = None
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1)
    received_at: datetime | None = None
    metadata: dict | None = None


class EmailConnectorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    smtp: dict | None = None
    imap: dict | None = None
    pop3: dict | None = None
    auth_config: dict | None = None


class EmailPollingJobRequest(BaseModel):
    target_id: UUID
    interval_seconds: int = Field(default=300, ge=1)
    interval_minutes: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, max_length=160)


# --------------------------------------------------------------------------
# Meta (Facebook/Instagram) Webhook Schemas
# --------------------------------------------------------------------------


class MetaMessagingEvent(BaseModel):
    """A single messaging event within a Meta webhook entry."""

    sender: dict | None = None
    recipient: dict | None = None
    timestamp: int | None = None
    message: dict | None = None
    postback: dict | None = None
    read: dict | None = None
    delivery: dict | None = None


class MetaWebhookEntry(BaseModel):
    """A single entry in a Meta webhook payload."""

    id: str  # Page ID or Instagram account ID
    time: int
    messaging: list[MetaMessagingEvent] | None = None
    changes: list[dict] | None = None  # For page posts/comments


class MetaWebhookPayload(BaseModel):
    """Full Meta webhook payload for Messenger/Instagram events."""

    object: Literal["page", "instagram"]
    entry: list[MetaWebhookEntry]


class FacebookMessengerWebhookPayload(BaseModel):
    """Parsed Facebook Messenger message (internal representation)."""

    contact_address: str = Field(min_length=1, max_length=255)  # Sender PSID
    contact_name: str | None = Field(default=None, max_length=160)
    message_id: str | None = Field(default=None, max_length=120)
    channel_target_id: UUID | None = None
    page_id: str  # Facebook Page ID that received the message
    body: str = Field(min_length=1)
    received_at: datetime | None = None
    metadata: dict | None = None


class InstagramDMWebhookPayload(BaseModel):
    """Parsed Instagram DM message (internal representation)."""

    contact_address: str = Field(min_length=1, max_length=255)  # Sender IGSID
    contact_name: str | None = Field(default=None, max_length=160)
    message_id: str | None = Field(default=None, max_length=120)
    channel_target_id: UUID | None = None
    instagram_account_id: str  # Instagram Business Account ID
    body: str | None = Field(default=None, min_length=1)
    received_at: datetime | None = None
    metadata: dict | None = None

    @model_validator(mode="after")
    def _require_body_for_non_story_mentions(self) -> InstagramDMWebhookPayload:
        if self.body:
            return self
        attachments = None
        if isinstance(self.metadata, dict):
            attachments = self.metadata.get("attachments")
        if _attachments_have_story_mention(attachments):
            return self
        raise ValueError("body is required for Instagram DM payloads")


def _attachments_have_story_mention(attachments: object) -> bool:
    if not isinstance(attachments, list):
        return False
    return any(isinstance(attachment, dict) and attachment.get("type") == "story_mention" for attachment in attachments)


class FacebookCommentPayload(BaseModel):
    """Facebook Page post comment webhook data."""

    post_id: str
    comment_id: str
    parent_id: str | None = None  # Parent comment ID for replies
    from_id: str
    from_name: str | None = None
    message: str
    created_time: datetime
    page_id: str


class InstagramCommentPayload(BaseModel):
    """Instagram post comment webhook data."""

    media_id: str
    comment_id: str
    from_id: str
    from_username: str | None = None
    text: str
    timestamp: datetime
    instagram_account_id: str
