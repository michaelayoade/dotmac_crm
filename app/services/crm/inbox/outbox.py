"""Outbox queue for async outbound messaging."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crm.outbox import OutboxMessage
from app.schemas.crm.inbox import InboxSendRequest
from app.services.common import coerce_uuid
from app.services.crm.inbox.errors import InboxError
from app.services.crm.inbox.outbound import (
    PermanentOutboundError,
    TransientOutboundError,
    send_message_with_retry,
)

STATUS_QUEUED = "queued"
STATUS_SENDING = "sending"
STATUS_RETRYING = "retrying"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"


def _now() -> datetime:
    return datetime.now(UTC)


def _compute_backoff_seconds(attempts: int, base: float = 5.0, max_backoff: float = 300.0) -> float:
    backoff = min(base * (2 ** max(attempts - 1, 0)), max_backoff)
    jitter = backoff * (secrets.randbelow(2500) / 10000)
    return backoff + jitter


def enqueue_outbound_message(
    db: Session,
    *,
    payload: InboxSendRequest,
    author_id: str | None,
    idempotency_key: str | None = None,
    priority: int = 0,
    scheduled_at: datetime | None = None,
    dispatch: bool = True,
    trace_id: str | None = None,
) -> OutboxMessage:
    if idempotency_key:
        idempotency_key = idempotency_key.strip() or None
    if idempotency_key:
        existing = (
            db.query(OutboxMessage)
            .filter(OutboxMessage.idempotency_key == idempotency_key)
            .order_by(OutboxMessage.created_at.desc())
            .first()
        )
        if existing:
            return existing

    next_attempt_at = scheduled_at if scheduled_at and scheduled_at > _now() else _now()
    payload_data = json.loads(payload.model_dump_json())
    if trace_id:
        payload_data.setdefault("metadata", {})
        if isinstance(payload_data.get("metadata"), dict):
            payload_data["metadata"]["trace_id"] = trace_id
    outbox = OutboxMessage(
        conversation_id=coerce_uuid(str(payload.conversation_id)),
        channel_type=payload.channel_type,
        status=STATUS_QUEUED,
        attempts=0,
        next_attempt_at=next_attempt_at,
        payload=payload_data,
        author_id=coerce_uuid(author_id) if author_id else None,
        idempotency_key=idempotency_key,
        priority=priority,
    )
    db.add(outbox)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            existing = (
                db.query(OutboxMessage)
                .filter(OutboxMessage.idempotency_key == idempotency_key)
                .order_by(OutboxMessage.created_at.desc())
                .first()
            )
            if existing:
                return existing
        raise
    db.refresh(outbox)

    if dispatch:
        from app.tasks.crm_inbox import send_outbox_item_task

        send_outbox_item_task.delay(str(outbox.id))

    return outbox


def process_outbox_item(db: Session, outbox_id: str) -> OutboxMessage:
    outbox = db.get(OutboxMessage, coerce_uuid(outbox_id))
    if not outbox:
        raise ValueError("Outbox item not found")

    if outbox.status in {STATUS_SENT, STATUS_FAILED}:
        return outbox

    if outbox.next_attempt_at and outbox.next_attempt_at > _now():
        return outbox

    outbox.status = STATUS_SENDING
    outbox.attempts = (outbox.attempts or 0) + 1
    outbox.last_attempt_at = _now()
    db.commit()
    db.refresh(outbox)

    try:
        trace_id = None
        if isinstance(outbox.payload, dict):
            metadata = outbox.payload.get("metadata")
            if isinstance(metadata, dict):
                trace_id = metadata.get("trace_id")
        message = send_message_with_retry(
            db,
            InboxSendRequest.model_validate(outbox.payload or {}),
            author_id=str(outbox.author_id) if outbox.author_id else None,
            trace_id=trace_id,
            max_attempts=2,
            base_backoff=0.5,
            max_backoff=2.0,
        )
        outbox.status = STATUS_SENT
        outbox.message_id = message.id
        outbox.last_error = None
        outbox.next_attempt_at = None
        db.commit()
        db.refresh(outbox)
        return outbox
    except TransientOutboundError as exc:
        outbox.status = STATUS_RETRYING
        outbox.last_error = str(exc)
        outbox.next_attempt_at = _now() + timedelta(seconds=_compute_backoff_seconds(outbox.attempts or 1))
        db.commit()
        db.refresh(outbox)
        raise
    except PermanentOutboundError as exc:
        outbox.status = STATUS_FAILED
        outbox.last_error = str(exc)
        outbox.next_attempt_at = None
        db.commit()
        db.refresh(outbox)
        return outbox
    except InboxError as exc:
        if exc.retryable:
            outbox.status = STATUS_RETRYING
            outbox.last_error = str(exc.detail)
            outbox.next_attempt_at = _now() + timedelta(seconds=_compute_backoff_seconds(outbox.attempts or 1))
            db.commit()
            db.refresh(outbox)
            raise TransientOutboundError(str(exc.detail))
        outbox.status = STATUS_FAILED
        outbox.last_error = str(exc.detail)
        outbox.next_attempt_at = None
        db.commit()
        db.refresh(outbox)
        return outbox
    except Exception as exc:
        outbox.status = STATUS_FAILED
        outbox.last_error = str(exc)
        outbox.next_attempt_at = None
        db.commit()
        db.refresh(outbox)
        return outbox


def list_due_outbox_ids(db: Session, *, limit: int = 50) -> list[str]:
    now = _now()
    items = (
        db.query(OutboxMessage)
        .filter(OutboxMessage.status.in_([STATUS_QUEUED, STATUS_RETRYING]))
        .filter((OutboxMessage.next_attempt_at.is_(None)) | (OutboxMessage.next_attempt_at <= now))
        .order_by(OutboxMessage.priority.desc(), OutboxMessage.created_at.asc())
        .limit(limit)
        .all()
    )
    return [str(item.id) for item in items]
