"""Inbound Email handler."""

from __future__ import annotations

import os
import uuid

from sqlalchemy.orm import Session

from app.logic.crm_inbox_logic import (
    InboundDedupeContext,
    InboundSelfMessageContext,
    LogicService,
)
from app.models.crm.conversation import Conversation
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.schemas.crm.conversation import ConversationCreate, MessageCreate
from app.schemas.crm.inbox import EmailWebhookPayload
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
    _is_self_email_message,
)
from app.services.crm.inbox.status_flow import apply_status_transition
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
    _metadata_indicates_comment,
)

logger = get_inbox_logger(__name__)
USE_INBOX_LOGIC_SERVICE = os.getenv("USE_INBOX_LOGIC_SERVICE", "0") == "1"
_logic_service = LogicService()
_self_detection = SelfDetectionService()


class EmailHandler(InboundHandler):
    def process(
        self,
        db: Session,
        payload: EmailWebhookPayload,
    ) -> InboundProcessResult | InboundDuplicateResult | InboundSkipResult | None:
        logger.debug(
            "receive_email_message_start subject=%s from=%s metadata_keys=%s",
            payload.subject,
            payload.contact_address,
            list(payload.metadata.keys()) if isinstance(payload.metadata, dict) else [],
        )
        dedupe_received_at = payload.received_at
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
                return InboundSkipResult(
                    channel_type=ChannelType.email.value,
                    reason="self_message",
                )
        else:
            if _is_self_email_message(payload, config):
                logger.info("email_inbound_skip_self from=%s", payload.contact_address)
                return InboundSkipResult(
                    channel_type=ChannelType.email.value,
                    reason="self_message",
                )

        account, _subscriber = None, None
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
                    dedupe_received_at,
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
            return InboundDuplicateResult(message=existing)

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
        try:
            person_uuid = coerce_uuid(person_id)
        except Exception:
            # Allow non-UUID identifiers in unit tests/mocks.
            person_uuid = None
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
                conv_meta = (
                    conversation.metadata_
                    if isinstance(conversation.metadata_, dict)
                    else {}
                )
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
            if isinstance(person_uuid, uuid.UUID):
                conversation_payload = ConversationCreate(
                    person_id=person_uuid,
                    subject=payload.subject,
                    is_active=True,
                )
            else:
                conversation_payload = ConversationCreate.model_construct(
                    person_id=person_id,
                    subject=payload.subject,
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
        elif conversation.status != ConversationStatus.open:
            check = apply_status_transition(conversation, ConversationStatus.open)
            if check.allowed:
                db.commit()
                db.refresh(conversation)
        elif conversation.person_id != person_uuid:
            logger.warning(
                "email_reply_conversation_mismatch conversation_id=%s sender_person_id=%s conversation_person_id=%s",
                conversation.id,
                person_id,
                conversation.person_id,
            )

        try:
            conversation_id = coerce_uuid(conversation.id)
            person_channel_id = coerce_uuid(channel.id)
            channel_target_id = coerce_uuid(target.id) if target else None
            message_payload = MessageCreate(
                conversation_id=conversation_id,
                person_channel_id=person_channel_id,
                channel_target_id=channel_target_id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                status=MessageStatus.received,
                subject=payload.subject,
                body=payload.body,
                external_id=external_id,
                received_at=received_at,
                metadata_=metadata,
            )
        except Exception:
            message_payload = MessageCreate.model_construct(
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
            )
        return InboundProcessResult(
            conversation_id=str(conversation.id),
            message_payload=message_payload,
            channel_target_id=str(target.id) if target else None,
        )
