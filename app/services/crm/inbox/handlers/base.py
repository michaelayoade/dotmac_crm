"""Base inbound handler strategy."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.crm.conversation import Message
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


def _is_new_inbound_message(db: Session, conversation_id: str) -> bool:
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.inbound)
        .count()
        <= 1
    )


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

        conversation_id = str(conversation.id)
        message_id = str(message.id)
        channel_target_id = result.channel_target_id
        is_new_conversation = message.direction == MessageDirection.inbound and _is_new_inbound_message(
            db,
            conversation_id,
        )

        def _after_commit(session):
            followup_db = SessionLocal()
            try:
                post_process_inbound_message(
                    followup_db,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    channel_target_id=channel_target_id,
                    is_new_conversation=is_new_conversation,
                )
                try:
                    from app.services.crm.campaigns import reconcile_outreach_inbound_reply

                    reconcile_outreach_inbound_reply(followup_db, message_id=message_id)
                except Exception:
                    logger.debug("inbound_outreach_reconcile_failed message_id=%s", message_id, exc_info=True)
            finally:
                followup_db.close()

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

            # Workqueue: clear "until next reply" snoozes and notify watchers.
            try:
                from app.services.workqueue.events import emit_change as _wq_emit
                from app.services.workqueue.snooze import workqueue_snooze
                from app.services.workqueue.types import ItemKind as _WQItemKind

                cleared_user_ids = workqueue_snooze.clear_until_next_reply_for_conversation(
                    db, conversation.id
                )
                if cleared_user_ids:
                    _wq_emit(
                        kind=_WQItemKind.conversation,
                        item_id=conversation.id,
                        change="added",
                        affected_user_ids=cleared_user_ids,
                    )
            except Exception as exc:
                logger.warning("inbound_workqueue_emit_failed error=%s", exc)

        MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="inbound").observe(
            time.perf_counter() - start
        )
        INBOUND_MESSAGES.labels(channel_type=channel_label, status="success").inc()
        return message
