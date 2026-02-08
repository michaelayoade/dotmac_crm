"""Admin UI helpers for CRM inbox."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.schemas.crm.conversation import ConversationCreate, MessageCreate
from app.schemas.crm.inbox import InboxSendRequest
from app.services.common import coerce_uuid
from app.services import crm as crm_service
from app.services.crm import conversations as conversation_service
from app.services.crm import contacts as contact_service
from app.services.crm.inbox.attachments_processing import apply_message_attachments
from app.services.crm.inbox.status_flow import apply_status_transition


@dataclass(frozen=True)
class SendConversationMessageResult:
    kind: Literal["not_found", "validation_error", "success", "send_failed"]
    conversation: Conversation | None = None
    message: Message | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class StartConversationResult:
    kind: Literal[
        "invalid_channel",
        "invalid_inbox",
        "missing_recipient",
        "missing_body",
        "send_failed",
        "error",
        "success",
    ]
    conversation_id: str | None = None
    error_detail: str | None = None


def _resolve_reply_channel_type(
    db: Session,
    conversation_id: str,
    conversation: Conversation,
) -> ChannelType:
    channel_type = conversation_service.get_reply_channel_type(db, conversation_id)
    if channel_type:
        return channel_type
    if conversation.contact and conversation.contact.channels:
        return ChannelType(conversation.contact.channels[0].channel_type.value)
    return ChannelType.email


def _resolve_channel_target_id(
    db: Session,
    conversation: Conversation,
) -> str | None:
    channel_target_id = None
    if isinstance(conversation.metadata_, dict):
        channel_target_id = conversation.metadata_.get("preferred_channel_target_id")
    if channel_target_id:
        return str(channel_target_id)

    last_with_target = (
        db.query(Message.channel_target_id)
        .filter(Message.conversation_id == conversation.id)
        .filter(Message.channel_target_id.isnot(None))
        .order_by(
            func.coalesce(
                Message.received_at,
                Message.sent_at,
                Message.created_at,
            ).desc()
        )
        .first()
    )
    if last_with_target and last_with_target[0]:
        return str(last_with_target[0])
    return None


def _apply_message_attachments(
    db: Session,
    message: Message,
    attachments: list[dict] | None,
) -> None:
    apply_message_attachments(db, message, attachments)


def send_conversation_message(
    db: Session,
    conversation_id: str,
    message_text: str | None,
    attachments_json: str | None,
    reply_to_message_id: str | None,
    author_id: str | None,
) -> SendConversationMessageResult:
    try:
        conversation = conversation_service.Conversations.get(db, conversation_id)
    except Exception:
        return SendConversationMessageResult(kind="not_found")

    channel_type = _resolve_reply_channel_type(db, conversation_id, conversation)
    channel_target_id = _resolve_channel_target_id(db, conversation)

    body_text = (message_text or "").strip()
    attachments_payload: list[dict] = []
    if attachments_json:
        try:
            attachments_payload = json.loads(attachments_json)
        except Exception:
            attachments_payload = []

    if not body_text and not attachments_payload:
        return SendConversationMessageResult(
            kind="validation_error",
            conversation=conversation,
            error_detail="Message or attachment is required.",
        )

    reply_to_uuid = None
    if reply_to_message_id:
        try:
            reply_to_uuid = coerce_uuid(reply_to_message_id)
        except Exception:
            reply_to_uuid = None

    channel_target_uuid = coerce_uuid(channel_target_id) if channel_target_id else None
    result_msg = None
    try:
        result_msg = crm_service.inbox.send_message_with_retry(
            db,
            InboxSendRequest(
                conversation_id=conversation.id,
                channel_type=channel_type,
                channel_target_id=channel_target_uuid,
                body=body_text,
                attachments=attachments_payload or None,
                reply_to_message_id=reply_to_uuid,
            ),
            author_id=author_id,
        )
    except Exception as exc:
        result_msg = conversation_service.Messages.create(
            db,
            MessageCreate(
                conversation_id=conversation.id,
                channel_type=channel_type,
                direction=MessageDirection.outbound,
                status=MessageStatus.failed,
                body=message_text,
                reply_to_message_id=reply_to_uuid,
                sent_at=datetime.now(timezone.utc),
            ),
        )
        if hasattr(exc, "detail"):
            error_detail = getattr(exc, "detail")
        else:
            error_detail = "Failed to send message"
        return SendConversationMessageResult(
            kind="send_failed",
            conversation=conversation,
            message=result_msg,
            error_detail=error_detail,
        )

    if result_msg:
        _apply_message_attachments(db, result_msg, attachments_payload)

    if result_msg and result_msg.status == MessageStatus.failed:
        return SendConversationMessageResult(
            kind="send_failed",
            conversation=conversation,
            message=result_msg,
            error_detail="Meta rejected the outbound message. Check logs.",
        )

    return SendConversationMessageResult(
        kind="success",
        conversation=conversation,
        message=result_msg,
    )


def start_new_conversation(
    db: Session,
    *,
    channel_type: str,
    channel_target_id: str | None,
    contact_address: str,
    contact_name: str | None,
    subject: str | None,
    message_text: str,
    author_person_id: str | None,
) -> StartConversationResult:
    try:
        channel_enum = ChannelType(channel_type)
    except ValueError:
        return StartConversationResult(
            kind="invalid_channel",
            error_detail="Invalid channel",
        )

    resolved_channel_target_id = None
    if channel_target_id:
        try:
            resolved_channel_target_id = coerce_uuid(channel_target_id)
        except Exception:
            return StartConversationResult(
                kind="invalid_inbox",
                error_detail="Invalid inbox selection",
            )

    address = contact_address.strip()
    if not address:
        return StartConversationResult(
            kind="missing_recipient",
            error_detail="Recipient is required",
        )

    body = message_text.strip()
    if not body:
        return StartConversationResult(
            kind="missing_body",
            error_detail="Message body is required",
        )

    contact, _ = contact_service.get_or_create_contact_by_channel(
        db,
        channel_enum,
        address,
        contact_name.strip() if contact_name else None,
    )
    conversation = conversation_service.resolve_open_conversation_for_channel(
        db, str(contact.id), channel_enum
    )
    if conversation and resolved_channel_target_id:
        last_inbound = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .filter(Message.direction == MessageDirection.inbound)
            .order_by(
                func.coalesce(
                    Message.received_at,
                    Message.sent_at,
                    Message.created_at,
                ).desc()
            )
            .first()
        )
        if last_inbound and last_inbound.channel_target_id:
            if last_inbound.channel_target_id != resolved_channel_target_id:
                conversation = None
    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=contact.id,
                subject=subject.strip() if subject and channel_enum == ChannelType.email else None,
            ),
        )
    if resolved_channel_target_id:
        metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
        metadata["preferred_channel_target_id"] = str(resolved_channel_target_id)
        conversation.metadata_ = metadata
        db.commit()

    try:
        result_msg = crm_service.inbox.send_message_with_retry(
            db,
            InboxSendRequest(
                conversation_id=conversation.id,
                channel_type=channel_enum,
                channel_target_id=resolved_channel_target_id,
                subject=subject.strip() if subject and channel_enum == ChannelType.email else None,
                body=body,
            ),
            author_id=author_person_id,
        )
        if result_msg and result_msg.status == MessageStatus.failed:
            return StartConversationResult(
                kind="send_failed",
                conversation_id=str(conversation.id),
                error_detail="Message failed to send",
            )
    except Exception as exc:
        detail = getattr(exc, "detail", None) or str(exc) or "Failed to send message"
        return StartConversationResult(
            kind="error",
            error_detail=detail,
        )

    if author_person_id:
        agent = (
            db.query(CrmAgent)
            .filter(CrmAgent.person_id == coerce_uuid(author_person_id))
            .filter(CrmAgent.is_active.is_(True))
            .first()
        )
        if agent:
            team_link = (
                db.query(CrmAgentTeam)
                .filter(CrmAgentTeam.agent_id == agent.id)
                .filter(CrmAgentTeam.is_active.is_(True))
                .order_by(CrmAgentTeam.created_at.desc())
                .first()
            )
            conversation_service.assign_conversation(
                db,
                conversation_id=str(conversation.id),
                agent_id=str(agent.id),
                team_id=str(team_link.team_id) if team_link and team_link.team_id else None,
                assigned_by_id=author_person_id,
                update_lead_owner=True,
            )
    if conversation.status != ConversationStatus.open:
        check = apply_status_transition(conversation, ConversationStatus.open)
        if check.allowed:
            db.commit()

    return StartConversationResult(
        kind="success",
        conversation_id=str(conversation.id),
    )
