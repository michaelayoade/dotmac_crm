"""Inbound message processing for CRM inbox.

Handles receiving messages from webhooks (WhatsApp, email) and routing
them to the appropriate conversation.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.logic.crm_inbox_logic import (
    ChannelType as LogicChannelType,
    LogicService,
    InboundSelfMessageContext,
    InboundDedupeContext,
)
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.team import CrmAgent
from app.schemas.crm.conversation import ConversationCreate, MessageCreate
from app.schemas.crm.inbox import EmailWebhookPayload, WhatsAppWebhookPayload
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox_connectors import _resolve_connector_config, _resolve_integration_target
from app.services.crm.inbox_contacts import _resolve_person_for_contact, _resolve_person_for_inbound
from app.services.crm.inbox_dedup import _build_inbound_dedupe_id, _find_duplicate_inbound_message
from app.services.crm.inbox_normalizers import _normalize_external_id
from app.services.crm.inbox_parsing import (
    _extract_conversation_tokens,
    _get_metadata_value,
    _resolve_conversation_from_email_metadata,
)
from app.services.crm.inbox_self_detection import (
    _extract_self_email_addresses,
    _extract_whatsapp_business_number,
    _is_self_email_message,
    _is_self_whatsapp_message,
    _metadata_indicates_comment,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)
USE_INBOX_LOGIC_SERVICE = os.getenv("USE_INBOX_LOGIC_SERVICE", "0") == "1"
_logic_service = LogicService()


def _now():
    return datetime.now(timezone.utc)


def _build_conversation_summary(db: Session, conversation, message: Message) -> dict:
    unread_count = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.status == MessageStatus.received)
        .filter(Message.read_at.is_(None))
        .count()
    )
    last_message_at = message.received_at or message.sent_at or message.created_at
    preview = message.body or ""
    if len(preview) > 100:
        preview = preview[:100] + "..."
    return {
        "preview": preview,
        "last_message_at": last_message_at.isoformat() if last_message_at else None,
        "channel": message.channel_type.value if message.channel_type else None,
        "unread_count": unread_count,
    }


def receive_whatsapp_message(db: Session, payload: WhatsAppWebhookPayload):
    """Process an inbound WhatsApp message from webhook.

    Args:
        db: Database session
        payload: WhatsApp webhook payload

    Returns:
        Message: The created or existing (duplicate) message
    """
    received_at = payload.received_at or _now()
    target = None
    if payload.channel_target_id:
        target = _resolve_integration_target(
            db,
            ChannelType.whatsapp,
            str(payload.channel_target_id),
        )
    else:
        target = _resolve_integration_target(db, ChannelType.whatsapp, None)

    config = _resolve_connector_config(db, target, ChannelType.whatsapp) if target else None
    if USE_INBOX_LOGIC_SERVICE:
        business_number = _extract_whatsapp_business_number(payload.metadata, config)
        self_ctx = InboundSelfMessageContext(
            channel_type="whatsapp",
            sender_address=payload.contact_address,
            metadata=payload.metadata,
            business_number=business_number,
        )
        if _logic_service.decide_inbound_self_message(self_ctx):
            logger.info(
                "whatsapp_inbound_skip_self contact_address=%s",
                payload.contact_address,
            )
            return None
    else:
        if _is_self_whatsapp_message(payload, config):
            logger.info(
                "whatsapp_inbound_skip_self contact_address=%s",
                payload.contact_address,
            )
            return None

    # Stub functions for removed billing - always return None
    account, subscriber = None, None

    person, channel = _resolve_person_for_inbound(
        db,
        ChannelType.whatsapp,
        payload.contact_address,
        payload.contact_name,
        account,
    )

    if USE_INBOX_LOGIC_SERVICE:
        dedupe_ctx = InboundDedupeContext(
            channel_type="whatsapp",
            contact_address=payload.contact_address,
            subject=None,
            body=payload.body,
            received_at_iso=received_at.isoformat() if received_at else None,
            message_id=payload.message_id,
        )
        dedupe_decision = _logic_service.decide_inbound_dedupe(dedupe_ctx)
        existing = _find_duplicate_inbound_message(
            db,
            ChannelType.whatsapp,
            channel.id,
            target.id if target else None,
            dedupe_decision.message_id,
            None,
            payload.body,
            received_at,
            dedupe_across_targets=dedupe_decision.dedupe_across_targets,
        )
    else:
        existing = _find_duplicate_inbound_message(
            db,
            ChannelType.whatsapp,
            channel.id,
            target.id if target else None,
            payload.message_id,
            None,
            payload.body,
            received_at,
        )
    if existing:
        return existing
    person_id = _resolve_person_for_contact(person)
    person_uuid = coerce_uuid(person_id)

    conversation = conversation_service.resolve_open_conversation_for_channel(
        db,
        person_id,
        ChannelType.whatsapp,
    )
    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=person_uuid,
                is_active=True,
            ),
        )
    elif not conversation.is_active:
        conversation.is_active = True
        conversation.status = ConversationStatus.open
        db.commit()
        db.refresh(conversation)
    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=channel.id,
            channel_target_id=target.id if target else None,
            channel_type=ChannelType.whatsapp,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            body=payload.body,
            external_id=payload.message_id,
            received_at=received_at,
            metadata_=payload.metadata,
        ),
    )
    from app.websocket.broadcaster import broadcast_new_message
    broadcast_new_message(message, conversation)
    from app.services.crm.inbox.notifications import notify_assigned_agent_new_reply
    notify_assigned_agent_new_reply(db, conversation, message)
    from app.websocket.broadcaster import broadcast_conversation_summary
    broadcast_conversation_summary(
        str(conversation.id),
        _build_conversation_summary(db, conversation, message),
    )
    from app.websocket.broadcaster import broadcast_inbox_updated
    agent_ids = (
        db.query(CrmAgent.person_id)
        .filter(CrmAgent.is_active.is_(True))
        .filter(CrmAgent.person_id.isnot(None))
        .distinct()
        .all()
    )
    inbox_payload = {
        "conversation_id": str(conversation.id),
        "message_id": str(message.id),
        "channel_target_id": str(target.id) if target else None,
        "last_message_at": (
            (message.received_at or message.created_at).isoformat()
            if message.received_at or message.created_at
            else None
        ),
    }
    for agent_id in agent_ids:
        broadcast_inbox_updated(str(agent_id[0]), inbox_payload)
    return message


def receive_email_message(db: Session, payload: EmailWebhookPayload):
    """Process an inbound email message from webhook.

    Args:
        db: Database session
        payload: Email webhook payload

    Returns:
        Message: The created or existing (duplicate) message
    """
    logger.debug(
        "receive_email_message_start subject=%s from=%s metadata_keys=%s",
        payload.subject,
        payload.contact_address,
        list(payload.metadata.keys()) if isinstance(payload.metadata, dict) else [],
    )
    received_at = payload.received_at or _now()
    metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
    target = None
    if payload.channel_target_id:
        target = _resolve_integration_target(
            db,
            ChannelType.email,
            str(payload.channel_target_id),
        )
    else:
        target = _resolve_integration_target(db, ChannelType.email, None)

    config = _resolve_connector_config(db, target, ChannelType.email) if target else None
    if USE_INBOX_LOGIC_SERVICE:
        self_addresses = _extract_self_email_addresses(config)
        self_ctx = InboundSelfMessageContext(
            channel_type="email",
            sender_address=payload.contact_address,
            metadata=payload.metadata,
            self_email_addresses=self_addresses,
        )
        if _logic_service.decide_inbound_self_message(self_ctx):
            logger.info("email_inbound_skip_self from=%s", payload.contact_address)
            return None
    else:
        if _is_self_email_message(payload, config):
            logger.info("email_inbound_skip_self from=%s", payload.contact_address)
            return None

    # Stub functions for removed billing - always return None
    account, subscriber = None, None

    person, channel = _resolve_person_for_inbound(
        db,
        ChannelType.email,
        payload.contact_address,
        payload.contact_name,
        account,
    )

    if USE_INBOX_LOGIC_SERVICE:
        dedupe_ctx = InboundDedupeContext(
            channel_type="email",
            contact_address=payload.contact_address,
            subject=payload.subject,
            body=payload.body,
            received_at_iso=received_at.isoformat() if received_at else None,
            message_id=payload.message_id,
        )
        dedupe_decision = _logic_service.decide_inbound_dedupe(dedupe_ctx)
        external_id = dedupe_decision.message_id
        existing = _find_duplicate_inbound_message(
            db,
            ChannelType.email,
            channel.id,
            target.id if target else None,
            external_id,
            payload.subject,
            payload.body,
            received_at,
            dedupe_across_targets=dedupe_decision.dedupe_across_targets,
        )
    else:
        external_id = _normalize_external_id(payload.message_id)
        if not external_id:
            external_id = _build_inbound_dedupe_id(
                ChannelType.email,
                payload.contact_address,
                payload.subject,
                payload.body,
                payload.received_at,
            )
        existing = _find_duplicate_inbound_message(
            db,
            ChannelType.email,
            channel.id,
            target.id if target else None,
            external_id,
            payload.subject,
            payload.body,
            received_at,
            dedupe_across_targets=True,
        )
    logger.info(
        "EMAIL_MESSAGE_RECEIVED subject=%s message_id=%s duplicate=%s",
        payload.subject,
        payload.message_id,
        existing is not None,
    )
    if existing:
        return existing
    person_id = _resolve_person_for_contact(person)

    in_reply_to = _get_metadata_value(metadata, "in_reply_to")
    references = _get_metadata_value(metadata, "references")
    crm_header = _get_metadata_value(metadata, "x-crm-id") or _get_metadata_value(
        metadata, "x-crm-conversation-id"
    )
    has_thread_headers = bool(
        payload.message_id
        or in_reply_to
        or references
        or crm_header
        or _extract_conversation_tokens(payload.subject)
    )

    conversation = _resolve_conversation_from_email_metadata(
        db,
        payload.subject,
        metadata,
    )
    person_uuid = coerce_uuid(person_id)
    if not conversation and _metadata_indicates_comment(metadata) and payload.subject:
        conversation = (
            db.query(Conversation)
            .filter(Conversation.person_id == person_uuid)
            .filter(Conversation.subject.ilike(payload.subject))
            .order_by(Conversation.updated_at.desc())
            .first()
        )
    if not conversation:
        conversation = conversation_service.resolve_open_conversation_for_channel(
            db,
            person_id,
            ChannelType.email,
        )
        if conversation and not has_thread_headers:
            conv_meta = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
            warnings = conv_meta.get("warnings")
            if not isinstance(warnings, list):
                warnings = []
            warnings.append(
                {
                    "type": "email_reply_without_headers",
                    "message_id": payload.message_id,
                    "received_at": received_at.isoformat() if received_at else None,
                }
            )
            conv_meta["warnings"] = warnings
            conversation.metadata_ = conv_meta
            db.commit()
    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=person_uuid,
                subject=payload.subject,
                is_active=True,
            ),
        )
    elif not conversation.is_active:
        conversation.is_active = True
        conversation.status = ConversationStatus.open
        db.commit()
        db.refresh(conversation)
    elif conversation.status != ConversationStatus.open:
        # Reopen conversations when a new inbound email arrives so they are visible in the inbox.
        conversation.status = ConversationStatus.open
        db.commit()
        db.refresh(conversation)
    elif conversation.person_id != person_uuid:
        logger.warning(
            "email_reply_conversation_mismatch conversation_id=%s sender_person_id=%s conversation_person_id=%s",
            conversation.id,
            person_id,
            conversation.person_id,
        )
    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=channel.id,
            channel_target_id=target.id if target else None,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            subject=payload.subject,
            body=payload.body,
            external_id=external_id,
            received_at=received_at,
            metadata_=metadata,
        ),
    )
    from app.websocket.broadcaster import broadcast_new_message
    broadcast_new_message(message, conversation)
    from app.services.crm.inbox.notifications import notify_assigned_agent_new_reply
    notify_assigned_agent_new_reply(db, conversation, message)
    from app.websocket.broadcaster import broadcast_conversation_summary
    broadcast_conversation_summary(
        str(conversation.id),
        _build_conversation_summary(db, conversation, message),
    )
    from app.websocket.broadcaster import broadcast_inbox_updated
    agent_ids = (
        db.query(CrmAgent.person_id)
        .filter(CrmAgent.is_active.is_(True))
        .filter(CrmAgent.person_id.isnot(None))
        .distinct()
        .all()
    )
    inbox_payload = {
        "conversation_id": str(conversation.id),
        "message_id": str(message.id),
        "channel_target_id": str(target.id) if target else None,
        "last_message_at": (
            (message.received_at or message.created_at).isoformat()
            if message.received_at or message.created_at
            else None
        ),
    }
    for agent_id in agent_ids:
        broadcast_inbox_updated(str(agent_id[0]), inbox_payload)
    return message


def receive_sms_message(db: Session, payload):
    # Placeholder for SMS inbound support.
    return None


def receive_chat_message(db: Session, payload):
    # Placeholder for chat inbound support.
    return None
