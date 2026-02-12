"""Inbound WhatsApp handler."""

from __future__ import annotations

import os
import uuid

from sqlalchemy.orm import Session

from app.logic.crm_inbox_logic import (
    InboundDedupeContext,
    InboundSelfMessageContext,
    LogicService,
)
from app.models.crm.conversation import Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.schemas.crm.conversation import ConversationCreate, MessageCreate
from app.schemas.crm.inbox import WhatsAppWebhookPayload
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.context import get_inbox_logger
from app.services.crm.inbox.handlers.base import (
    InboundDuplicateResult,
    InboundHandler,
    InboundProcessResult,
    InboundSkipResult,
)
from app.services.crm.inbox.handlers.utils import _now
from app.services.crm.inbox.self_detection import (
    SelfDetectionService,
    _is_self_whatsapp_message,
)
from app.services.crm.inbox.status_flow import apply_status_transition
from app.services.crm.inbox_connectors import _resolve_connector_config, _resolve_integration_target
from app.services.crm.inbox_contacts import _resolve_person_for_contact, _resolve_person_for_inbound
from app.services.crm.inbox_dedup import _find_duplicate_inbound_message
from app.services.crm.inbox_self_detection import _extract_whatsapp_business_number

logger = get_inbox_logger(__name__)
USE_INBOX_LOGIC_SERVICE = os.getenv("USE_INBOX_LOGIC_SERVICE", "0") == "1"
_logic_service = LogicService()
_self_detection = SelfDetectionService()


class WhatsAppHandler(InboundHandler):
    def process(
        self,
        db: Session,
        payload: WhatsAppWebhookPayload,
    ) -> InboundProcessResult | InboundDuplicateResult | InboundSkipResult | None:
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
                return InboundSkipResult(
                    channel_type=ChannelType.whatsapp.value,
                    reason="self_message",
                )
        else:
            if _is_self_whatsapp_message(payload, config):
                logger.info(
                    "whatsapp_inbound_skip_self contact_address=%s",
                    payload.contact_address,
                )
                return InboundSkipResult(
                    channel_type=ChannelType.whatsapp.value,
                    reason="self_message",
                )

        account, _subscriber = None, None
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
            return InboundDuplicateResult(message=existing)

        person_id = _resolve_person_for_contact(person)
        try:
            person_uuid = coerce_uuid(person_id)
        except Exception:
            # Allow non-UUID identifiers in unit tests/mocks.
            person_uuid = None

        conversation = conversation_service.resolve_open_conversation_for_channel(
            db,
            person_id,
            ChannelType.whatsapp,
        )
        if not conversation:
            if isinstance(person_uuid, uuid.UUID):
                conversation_payload = ConversationCreate(
                    person_id=person_uuid,
                    is_active=True,
                )
            else:
                conversation_payload = ConversationCreate.model_construct(
                    person_id=person_id,
                    is_active=True,
                )
            conversation = conversation_service.Conversations.create(
                db,
                conversation_payload,
            )
        elif not conversation.is_active:
            conversation.is_active = True
            apply_status_transition(conversation, ConversationStatus.open)
            db.commit()
            db.refresh(conversation)

        reply_to_message_id = None
        context_message_id = None
        if isinstance(payload.metadata, dict):
            context_message_id = payload.metadata.get("context_message_id")
        if context_message_id:
            replied = (
                db.query(Message)
                .filter(Message.external_id == context_message_id)
                .filter(Message.channel_type == ChannelType.whatsapp)
                .order_by(Message.created_at.desc())
                .first()
            )
            if replied and replied.conversation_id == conversation.id:
                reply_to_message_id = replied.id

        try:
            conversation_id = coerce_uuid(conversation.id)
            person_channel_id = coerce_uuid(channel.id)
            channel_target_id = coerce_uuid(target.id) if target else None
            message_payload = MessageCreate(
                conversation_id=conversation_id,
                person_channel_id=person_channel_id,
                channel_target_id=channel_target_id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                status=MessageStatus.received,
                body=payload.body,
                external_id=payload.message_id,
                received_at=received_at,
                metadata_=payload.metadata,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception:
            message_payload = MessageCreate.model_construct(
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
                reply_to_message_id=reply_to_message_id,
            )
        return InboundProcessResult(
            conversation_id=str(conversation.id),
            message_payload=message_payload,
            channel_target_id=str(target.id) if target else None,
        )
