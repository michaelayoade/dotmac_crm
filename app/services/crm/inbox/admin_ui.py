"""Admin UI helpers for CRM inbox."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.models.person import ChannelType as PersonChannelType
from app.schemas.crm.conversation import ConversationCreate, MessageCreate
from app.schemas.crm.inbox import InboxSendRequest
from app.services import crm as crm_service
from app.services.common import coerce_uuid
from app.services.crm.contacts.service import get_or_create_contact_by_channel
from app.services.crm.conversations import service as conversation_service
from app.services.crm.inbox.attachments_processing import apply_message_attachments
from app.services.crm.inbox.permissions import can_send_message, can_write_inbox
from app.services.crm.inbox.status_flow import apply_status_transition

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SendConversationMessageResult:
    kind: Literal["forbidden", "not_found", "validation_error", "success", "send_failed"]
    conversation: Conversation | None = None
    message: Message | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class StartConversationResult:
    kind: Literal[
        "forbidden",
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
    idempotency_key: str | None,
    reply_to_message_id: str | None,
    template_id: str | None = None,
    scheduled_at: str | None = None,
    author_id: str | None = None,
    trace_id: str | None = None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> SendConversationMessageResult:
    if (roles is not None or scopes is not None) and not can_send_message(roles, scopes):
        return SendConversationMessageResult(
            kind="forbidden",
            error_detail="Not authorized to send messages",
        )
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

    if not body_text and not attachments_payload and not template_id:
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
    template_uuid = None
    if template_id:
        try:
            template_uuid = coerce_uuid(template_id)
        except Exception:
            template_uuid = None

    scheduled_value = None
    if scheduled_at:
        try:
            scheduled_value = datetime.fromisoformat(scheduled_at)
            if scheduled_value.tzinfo is None:
                scheduled_value = scheduled_value.replace(tzinfo=UTC)
        except Exception:
            scheduled_value = None
    normalized_idempotency_key = (idempotency_key or "").strip() or None
    result_msg = None
    skip_attachment_apply = False
    try:
        payload = InboxSendRequest(
            conversation_id=conversation.id,
            channel_type=channel_type,
            channel_target_id=channel_target_uuid,
            body=body_text,
            attachments=attachments_payload or None,
            reply_to_message_id=reply_to_uuid,
            template_id=template_uuid,
            scheduled_at=scheduled_value,
        )
        if scheduled_value and scheduled_value > datetime.now(UTC):
            from app.services.crm.inbox.outbox import enqueue_outbound_message

            enqueue_outbound_message(
                db,
                payload=payload,
                author_id=author_id,
                idempotency_key=normalized_idempotency_key,
                scheduled_at=scheduled_value,
                dispatch=True,
                trace_id=trace_id,
            )
            return SendConversationMessageResult(
                kind="success",
                conversation=conversation,
                message=None,
            )

        if normalized_idempotency_key:
            from app.services.crm.inbox import outbox as outbox_service

            outbox_item = outbox_service.enqueue_outbound_message(
                db,
                payload=payload,
                author_id=author_id,
                idempotency_key=normalized_idempotency_key,
                dispatch=False,
                trace_id=trace_id,
            )
            if outbox_item.status == outbox_service.STATUS_SENT and outbox_item.message_id:
                result_msg = db.get(Message, outbox_item.message_id)
                # Existing key was already processed; avoid duplicating attachment rows.
                skip_attachment_apply = True
            elif outbox_item.status == outbox_service.STATUS_FAILED:
                return SendConversationMessageResult(
                    kind="send_failed",
                    conversation=conversation,
                    message=None,
                    error_detail=outbox_item.last_error or "Failed to send message",
                )
            else:
                processed = outbox_service.process_outbox_item(db, str(outbox_item.id))
                if processed.status == outbox_service.STATUS_SENT and processed.message_id:
                    result_msg = db.get(Message, processed.message_id)
                elif processed.status == outbox_service.STATUS_FAILED:
                    return SendConversationMessageResult(
                        kind="send_failed",
                        conversation=conversation,
                        message=None,
                        error_detail=processed.last_error or "Failed to send message",
                    )
                else:
                    return SendConversationMessageResult(
                        kind="success",
                        conversation=conversation,
                        message=None,
                    )
        else:
            result_msg = crm_service.inbox.send_message_with_retry(
                db,
                payload,
                author_id=author_id,
                trace_id=trace_id,
            )
    except Exception as exc:
        error_detail = getattr(exc, "detail", None) or "Failed to send message"
        if "Rate limit exceeded" in str(error_detail):
            retry_after = 60
            if "retry_after=" in str(error_detail):
                try:
                    retry_part = str(error_detail).split("retry_after=")[-1]
                    retry_after = int(retry_part.split("s")[0])
                except Exception:
                    retry_after = 60
            scheduled_value = datetime.now(UTC) + timedelta(seconds=retry_after)
            from app.services.crm.inbox.outbox import enqueue_outbound_message

            enqueue_outbound_message(
                db,
                payload=payload,
                author_id=author_id,
                idempotency_key=normalized_idempotency_key,
                scheduled_at=scheduled_value,
                dispatch=True,
            )
            return SendConversationMessageResult(
                kind="success",
                conversation=conversation,
                message=None,
            )
        result_msg = conversation_service.Messages.create(
            db,
            MessageCreate(
                conversation_id=conversation.id,
                channel_type=channel_type,
                direction=MessageDirection.outbound,
                status=MessageStatus.failed,
                body=message_text,
                reply_to_message_id=reply_to_uuid,
                sent_at=datetime.now(UTC),
            ),
        )
        return SendConversationMessageResult(
            kind="send_failed",
            conversation=conversation,
            message=result_msg,
            error_detail=error_detail,
        )

    if result_msg and not skip_attachment_apply:
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
    contact_id: str | None,
    contact_address: str,
    contact_name: str | None,
    subject: str | None,
    message_text: str | None,
    whatsapp_template_name: str | None = None,
    whatsapp_template_language: str | None = None,
    whatsapp_template_components: str | None = None,
    author_person_id: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> StartConversationResult:
    if (roles is not None or scopes is not None) and not can_write_inbox(roles, scopes):
        return StartConversationResult(
            kind="forbidden",
            error_detail="Not authorized to start conversations",
        )
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

    body = (message_text or "").strip()
    template_name = (whatsapp_template_name or "").strip() or None
    template_language = (whatsapp_template_language or "").strip() or None
    template_components: list[dict] | None = None
    if whatsapp_template_components:
        try:
            parsed = json.loads(whatsapp_template_components)
            if isinstance(parsed, list):
                template_components = parsed
        except Exception:
            logger.warning("whatsapp_template_components_parse_failed raw=%s", whatsapp_template_components[:200])
            template_components = None
    if channel_enum == ChannelType.whatsapp:
        if not template_name:
            return StartConversationResult(
                kind="missing_body",
                error_detail="WhatsApp template is required for new conversations",
            )
        if not template_language:
            return StartConversationResult(
                kind="missing_body",
                error_detail="WhatsApp template language is required",
            )
        if whatsapp_template_components and template_components is None:
            return StartConversationResult(
                kind="missing_body",
                error_detail="WhatsApp template parameters are invalid",
            )
    elif not body:
        return StartConversationResult(
            kind="missing_body",
            error_detail="Message body is required",
        )

    contact = None
    selected_person_channel = None
    if channel_enum == ChannelType.whatsapp:
        contact_id_value = (contact_id or "").strip()
        if not contact_id_value:
            return StartConversationResult(
                kind="missing_recipient",
                error_detail="Contact is required for WhatsApp conversations",
            )
        contact = crm_service.contacts.get(db, contact_id_value)
        channels = [
            ch for ch in (contact.channels or []) if ch.channel_type == PersonChannelType.whatsapp and ch.address
        ]
        if not channels:
            return StartConversationResult(
                kind="missing_recipient",
                error_detail="Selected contact has no WhatsApp number",
            )
        primary = next((ch for ch in channels if ch.is_primary), None)
        selected_person_channel = primary or channels[0]
        address = selected_person_channel.address
        contact_name = contact.display_name or contact_name
    else:
        address = contact_address.strip()
        if not address:
            return StartConversationResult(
                kind="missing_recipient",
                error_detail="Recipient is required",
            )
        contact, _ = get_or_create_contact_by_channel(
            db,
            channel_enum,
            address,
            contact_name.strip() if contact_name else None,
        )
    conversation = conversation_service.resolve_open_conversation_for_channel(db, str(contact.id), channel_enum)
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
        if (
            last_inbound
            and last_inbound.channel_target_id
            and last_inbound.channel_target_id != resolved_channel_target_id
        ):
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
                person_channel_id=selected_person_channel.id if selected_person_channel else None,
                subject=subject.strip() if subject and channel_enum == ChannelType.email else None,
                body=body,
                whatsapp_template_name=template_name,
                whatsapp_template_language=template_language,
                whatsapp_template_components=template_components,
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
