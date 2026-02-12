"""Inbox health metrics helpers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationStatus
from app.models.crm.outbox import OutboxMessage
from app.services.crm.inbox import outbox as outbox_service


def _now() -> datetime:
    return datetime.now(UTC)


def _coerce_count(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int | float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def summarize_conversation_status_rows(rows: Sequence[tuple[object, object]]) -> dict[str, int]:
    counts = {status.value: 0 for status in ConversationStatus}
    total = 0
    for status, count in rows or []:
        key = status.value if isinstance(status, ConversationStatus) else str(status)
        value = _coerce_count(count)
        counts[key] = value
        total += value
    counts["total"] = total
    return counts


def summarize_outbox_status_rows(rows: Sequence[tuple[object, object]]) -> dict[str, int]:
    statuses = {
        outbox_service.STATUS_QUEUED,
        outbox_service.STATUS_SENDING,
        outbox_service.STATUS_RETRYING,
        outbox_service.STATUS_SENT,
        outbox_service.STATUS_FAILED,
    }
    counts = {status: 0 for status in statuses}
    total = 0
    for status, count in rows or []:
        key = str(status)
        value = _coerce_count(count)
        counts[key] = value
        total += value
    counts["total"] = total
    return counts


def get_conversation_status_counts(db: Session) -> dict[str, int]:
    raw_rows = (
        db.query(Conversation.status, func.count(Conversation.id))
        .filter(Conversation.is_active.is_(True))
        .group_by(Conversation.status)
        .all()
    )
    rows = [(row[0], row[1]) for row in raw_rows]
    return summarize_conversation_status_rows(rows)


def get_outbox_status_counts(db: Session) -> dict[str, int]:
    raw_rows = db.query(OutboxMessage.status, func.count(OutboxMessage.id)).group_by(OutboxMessage.status).all()
    rows = [(row[0], row[1]) for row in raw_rows]
    return summarize_outbox_status_rows(rows)


def get_outbox_due_count(db: Session) -> int:
    now = _now()
    return (
        db.query(OutboxMessage.id)
        .filter(OutboxMessage.status.in_([outbox_service.STATUS_QUEUED, outbox_service.STATUS_RETRYING]))
        .filter((OutboxMessage.next_attempt_at.is_(None)) | (OutboxMessage.next_attempt_at <= now))
        .count()
    )


def get_inbox_metrics(db: Session) -> dict[str, dict[str, int]]:
    return {
        "conversations": get_conversation_status_counts(db),
        "outbox": {
            **get_outbox_status_counts(db),
            "due": get_outbox_due_count(db),
        },
    }
