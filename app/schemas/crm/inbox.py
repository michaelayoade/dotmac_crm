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
    bcc_addresses: list[EmailStr] | None = Field(default=None, max_length=MAX_CC_ADDRESSES)
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


class WhatsAppCallActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=20)
    to: str | None = Field(default=None, max_length=255)
    target_id: UUID | None = None
    phone_number_id: str | None = Field(default=None, max_length=80)
    sdp: str | None = Field(default=None, min_length=1)
    sdp_type: str | None = Field(default=None, min_length=1, max_length=20)
    session: dict | None = None

    @model_validator(mode="after")
    def _fallback_to_legacy_top_level_session(self):
        if self.session is None and (self.sdp is not None or self.sdp_type is not None):
            if self.sdp is None or self.sdp_type is None:
                raise ValueError("Both sdp and sdp_type are required when session is not provided.")
            self.session = {"sdp_type": self.sdp_type.strip(), "sdp": self.sdp}
        if self.session is not None:
            if not isinstance(self.session, dict):
                raise ValueError("session must be an object.")
            session_sdp_type = self.session.get("sdp_type")
            session_sdp = self.session.get("sdp")
            if session_sdp_type is not None or session_sdp is not None:
                if not isinstance(session_sdp_type, str) or not session_sdp_type.strip():
                    raise ValueError("session.sdp_type must be a non-empty string when session is provided.")
                if not isinstance(session_sdp, str) or not session_sdp.strip():
                    raise ValueError("session.sdp must be a non-empty string when session is provided.")
                self.session["sdp_type"] = session_sdp_type.strip()
                self.session["sdp"] = session_sdp
                if self.sdp is not None:
                    self.sdp = session_sdp
                if self.sdp_type is not None:
                    self.sdp_type = session_sdp_type.strip()
        return self


class WhatsAppCallActionResponse(BaseModel):
    call_id: str
    action: str
    phone_number_id: str
    status_code: int | None = None
    provider_response: dict | list | str | None = None


class WhatsAppCallContextResponse(BaseModel):
    call_id: str
    phone_number_id: str | None = None
    display_phone_number: str | None = None
    call_status: str | None = None
    call_direction: str | None = None
    to: str | None = None
    from_: str | None = Field(default=None, alias="from")
    session: dict | None = None

    model_config = {"populate_by_name": True}


class WhatsAppWebrtcConfigResponse(BaseModel):
    ice_servers: list[dict] = Field(default_factory=list)


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
    """Full Meta webhook payload for Messenger, Instagram, and WhatsApp Business events."""

    object: Literal["page", "instagram", "whatsapp_business_account"]
    entry: list[MetaWebhookEntry]


# --------------------------------------------------------------------------
# WhatsApp Business API Status Schemas
# --------------------------------------------------------------------------


class WhatsAppStatusUpdate(BaseModel):
    """A single status update from the WhatsApp Business API."""

    id: str  # wamid of the original outbound message
    # Keep status as a plain string for forward-compatible status payloads
    # from WhatsApp (e.g. call/experimental statuses). Unknown values are
    # ignored by the message-status processor.
    status: str
    timestamp: str
    recipient_id: str
    errors: list[dict] | None = None


class WhatsAppStatusValue(BaseModel):
    """The 'value' object inside a WhatsApp Business webhook change."""

    messaging_product: str | None = None
    metadata: dict | None = None
    statuses: list[WhatsAppStatusUpdate] | None = None
    messages: list[dict] | None = None
    contacts: list[dict] | None = None


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
