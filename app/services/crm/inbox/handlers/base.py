"""Base inbound handler strategy."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.crm.enums import MessageDirection
from app.schemas.crm.conversation import MessageCreate
from app.services.crm.inbox.context import get_inbox_logger, set_request_id
from app.services.crm.inbox.handlers.utils import (
    create_message_and_touch_conversation,
    post_process_inbound_message,
)
from app.services.crm.inbox.observability import INBOUND_MESSAGES, MESSAGE_PROCESSING_TIME
from app.services.events import EventType, emit_event

logger = get_inbox_logger(__name__)


@dataclass(frozen=True)
class InboundProcessResult:
    conversation_id: str
    message_payload: MessageCreate
    channel_target_id: str | None


@dataclass(frozen=True)
class InboundDuplicateResult:
    message: Any


@dataclass(frozen=True)
class InboundSkipResult:
    channel_type: str
    reason: str


class InboundHandler:
    def process(self, db: Session, payload) -> InboundProcessResult | InboundDuplicateResult | InboundSkipResult | None:
        raise NotImplementedError

    def receive(self, db: Session, payload):
        set_request_id()
        start = time.perf_counter()
        channel_label = "unknown"
        try:
            result = self.process(db, payload)
        except Exception:
            MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="inbound").observe(
                time.perf_counter() - start
            )
            INBOUND_MESSAGES.labels(channel_type=channel_label, status="error").inc()
            raise
        if result is None:
            MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="inbound").observe(
                time.perf_counter() - start
            )
            INBOUND_MESSAGES.labels(channel_type=channel_label, status="error").inc()
            return None
        if isinstance(result, InboundSkipResult):
            channel_label = result.channel_type
            MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="inbound").observe(
                time.perf_counter() - start
            )
            INBOUND_MESSAGES.labels(channel_type=channel_label, status=result.reason).inc()
            return None
        if isinstance(result, InboundDuplicateResult):
            if getattr(result.message, "channel_type", None):
                channel_label = result.message.channel_type.value
            MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="inbound").observe(
                time.perf_counter() - start
            )
            INBOUND_MESSAGES.labels(channel_type=channel_label, status="duplicate").inc()
            return result.message

        message_data = result.message_payload.model_dump(by_alias=False)
        channel_type_value = message_data.get("channel_type")
        channel_label_value = getattr(channel_type_value, "value", None)
        if isinstance(channel_label_value, str):
            channel_label = channel_label_value
        elif isinstance(channel_type_value, str):
            channel_label = channel_type_value
        else:
            channel_label = "unknown"
        conversation, message = create_message_and_touch_conversation(
            db,
            conversation_id=result.conversation_id,
            payload=message_data,
        )

        def _after_commit(session):
            post_process_inbound_message(
                db,
                conversation_id=str(conversation.id),
                message_id=str(message.id),
                channel_target_id=result.channel_target_id,
            )

        event.listen(db, "after_commit", _after_commit, once=True)
        db.commit()
        db.refresh(message)

        if message.direction == MessageDirection.inbound:
            try:
                emit_event(
                    db,
                    EventType.message_inbound,
                    {
                        "message_id": str(message.id),
                        "conversation_id": str(message.conversation_id),
                        "person_id": str(conversation.person_id),
                        "channel_type": message.channel_type.value,
                        "channel_target_id": (str(message.channel_target_id) if message.channel_target_id else None),
                        "subject": message.subject,
                        "external_id": message.external_id,
                    },
                )
            except Exception as exc:
                logger.warning("inbound_emit_event_failed error=%s", exc)

        MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="inbound").observe(
            time.perf_counter() - start
        )
        INBOUND_MESSAGES.labels(channel_type=channel_label, status="success").inc()
        return message
