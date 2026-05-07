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
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.schemas.crm.conversation import ConversationCreate, MessageCreate
from app.schemas.crm.inbox import WhatsAppWebhookPayload
from app.services import meta_webhooks
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.context import get_inbox_logger
from app.services.crm.inbox.conversation_status import reopen_snooze_on_next_reply
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
_CALL_CONNECT_STATES = {"connect", "ringing", "ring", "incoming", "invited", "calling"}
_CALL_TERMINAL_STATES = {
    "completed",
    "ended",
    "terminated",
    "rejected",
    "failed",
    "missed",
    "busy",
    "no_answer",
    "canceled",
    "cancelled",
    "timeout",
    "terminate",
}
_CALL_EVENT_EXTERNAL_ID_MAX_LEN = 120
_CALL_EVENT_DELIMITER = "::"


def _extract_whatsapp_meta_attribution(metadata: dict | None) -> dict | None:
    if not isinstance(metadata, dict):
        return None
    attribution = metadata.get("attribution")
    return dict(attribution) if isinstance(attribution, dict) and attribution else None


def _extract_call_signal(metadata: dict | None) -> tuple[str | None, str | None, str | None]:
    if not isinstance(metadata, dict):
        return None, None, None
    raw_call = metadata.get("call")
    call_obj = raw_call if isinstance(raw_call, dict) else {}
    call_id = metadata.get("call_id") or call_obj.get("call_id") or call_obj.get("id")
    call_status = (
        metadata.get("call_status") or call_obj.get("call_status") or call_obj.get("event") or call_obj.get("status")
    )
    call_reason = call_obj.get("reason") or call_obj.get("termination_reason")
    normalized_call_id = str(call_id).strip() if isinstance(call_id, str) and call_id.strip() else None
    normalized_status = (
        str(call_status).strip().lower() if isinstance(call_status, str) and call_status.strip() else None
    )
    normalized_reason = str(call_reason).strip() if isinstance(call_reason, str) and call_reason.strip() else None
    return normalized_call_id, normalized_status, normalized_reason


def _build_call_event_external_id(call_id: str | None, call_status: str | None) -> str | None:
    normalized_call_id = call_id.strip() if isinstance(call_id, str) else ""
    normalized_status = call_status.strip().lower() if isinstance(call_status, str) else ""
    if not normalized_call_id:
        return None
    if not normalized_status or normalized_status in _CALL_CONNECT_STATES:
        return normalized_call_id
    suffix = f"{_CALL_EVENT_DELIMITER}{normalized_status}"
    if len(normalized_call_id) + len(suffix) <= _CALL_EVENT_EXTERNAL_ID_MAX_LEN:
        return f"{normalized_call_id}{suffix}"
    base_max_len = max(_CALL_EVENT_EXTERNAL_ID_MAX_LEN - len(suffix), 1)
    return f"{normalized_call_id[:base_max_len]}{suffix}"


def _merge_call_actor_metadata(
    incoming_metadata: dict | None,
    previous_metadata: dict | None,
) -> dict | None:
    if not isinstance(incoming_metadata, dict):
        return incoming_metadata

    previous = previous_metadata if isinstance(previous_metadata, dict) else {}
    if not previous:
        return incoming_metadata

    merged = dict(incoming_metadata)
    for key in ("accepted_by_person_id", "accepted_by_name"):
        if not merged.get(key) and previous.get(key):
            merged[key] = previous.get(key)

    incoming_call_raw = merged.get("call")
    incoming_call = incoming_call_raw if isinstance(incoming_call_raw, dict) else {}
    previous_call_raw = previous.get("call")
    previous_call = previous_call_raw if isinstance(previous_call_raw, dict) else {}
    if previous_call:
        merged_call = dict(incoming_call)
        for key in ("accepted_by_person_id", "accepted_by_name"):
            if not merged_call.get(key) and previous_call.get(key):
                merged_call[key] = previous_call.get(key)
        merged["call"] = merged_call

    return merged


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
        call_id, call_status, call_reason = _extract_call_signal(payload.metadata)
        call_event_external_id = _build_call_event_external_id(call_id, call_status)
        if call_id and call_status and existing and isinstance(existing.metadata_, dict):
            _, existing_call_status, _ = _extract_call_signal(existing.metadata_)
            # For call events, only treat exact repeated status as duplicate.
            if existing_call_status and existing_call_status != call_status:
                existing = None
        if call_event_external_id:
            status_scoped_existing = (
                db.query(Message)
                .filter(Message.channel_type == ChannelType.whatsapp)
                .filter(Message.external_id == call_event_external_id)
                .order_by(Message.received_at.desc().nullslast(), Message.created_at.desc())
                .first()
            )
            if status_scoped_existing:
                return InboundDuplicateResult(message=status_scoped_existing)
        if existing:
            return InboundDuplicateResult(message=existing)

        previous_call_message = None
        previous_call_status = None
        previous_call_at = None
        if call_id and call_status:
            previous_call_message = (
                db.query(Message)
                .filter(Message.channel_type == ChannelType.whatsapp)
                .filter(Message.external_id == call_id)
                .order_by(Message.received_at.desc().nullslast(), Message.created_at.desc())
                .first()
            )
            if previous_call_message and isinstance(previous_call_message.metadata_, dict):
                _, previous_call_status, _ = _extract_call_signal(previous_call_message.metadata_)
                payload.metadata = _merge_call_actor_metadata(payload.metadata, previous_call_message.metadata_)
            previous_call_at = (
                previous_call_message.received_at or previous_call_message.created_at if previous_call_message else None
            )

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
        if not conversation and hasattr(db, "query"):
            conversation = (
                db.query(Conversation)
                .filter(Conversation.person_id == person_uuid)
                .filter(Conversation.status == ConversationStatus.snoozed)
                .order_by(Conversation.updated_at.desc())
                .first()
            )
            if conversation and reopen_snooze_on_next_reply(conversation):
                db.commit()
                db.refresh(conversation)
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

        attribution = _extract_whatsapp_meta_attribution(payload.metadata)
        if attribution:
            meta_webhooks._upsert_entity_attribution_metadata(
                conversation,
                attribution=attribution,
                channel=ChannelType.whatsapp,
            )
            meta_webhooks._persist_meta_attribution_to_person_and_lead(
                db,
                person=person,
                channel=ChannelType.whatsapp,
                attribution=attribution,
            )

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
                external_id=call_event_external_id or payload.message_id,
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
                external_id=call_event_external_id or payload.message_id,
                received_at=received_at,
                metadata_=payload.metadata,
                reply_to_message_id=reply_to_message_id,
            )
        if call_id and call_status:
            delta_s = None
            if previous_call_at and received_at:
                try:
                    delta_s = round((received_at - previous_call_at).total_seconds(), 3)
                except Exception:
                    delta_s = None
            logger.info(
                "whatsapp_call_lifecycle call_id=%s status=%s prev_status=%s delta_s=%s reason=%s",
                call_id,
                call_status,
                previous_call_status,
                delta_s,
                call_reason,
            )
            if (
                call_status in _CALL_TERMINAL_STATES
                and previous_call_status in _CALL_CONNECT_STATES
                and delta_s is not None
                and delta_s <= 30
            ):
                logger.warning(
                    "whatsapp_call_ended_while_connecting call_id=%s prev_status=%s status=%s delta_s=%s reason=%s",
                    call_id,
                    previous_call_status,
                    call_status,
                    delta_s,
                    call_reason,
                )
        return InboundProcessResult(
            conversation_id=str(conversation.id),
            message_payload=message_payload,
            channel_target_id=str(target.id) if target else None,
        )
