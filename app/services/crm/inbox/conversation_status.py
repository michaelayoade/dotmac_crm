"""Conversation status helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationPriority, ConversationStatus
from app.schemas.crm.conversation import ConversationUpdate
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox.audit import log_conversation_action
from app.services.crm.inbox.csat import queue_for_resolved_conversation
from app.services.crm.inbox.permissions import can_update_conversation_status
from app.services.crm.inbox.status_flow import validate_transition

SNOOZE_METADATA_KEY = "snooze"


def _metadata_dict(conversation: Conversation) -> dict:
    existing = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
    return dict(existing)


def _clear_snooze_metadata(conversation: Conversation) -> None:
    metadata = _metadata_dict(conversation)
    if SNOOZE_METADATA_KEY in metadata:
        metadata.pop(SNOOZE_METADATA_KEY, None)
        conversation.metadata_ = metadata


def _extract_snooze(conversation: Conversation) -> dict | None:
    if not isinstance(conversation.metadata_, dict):
        return None
    raw = conversation.metadata_.get(SNOOZE_METADATA_KEY)
    return raw if isinstance(raw, dict) else None


def _parse_snooze_until(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _apply_snooze(
    conversation: Conversation,
    *,
    now: datetime,
    mode: str,
    until_at: datetime | None,
    actor_id: str | None,
) -> None:
    metadata = _metadata_dict(conversation)
    metadata[SNOOZE_METADATA_KEY] = {
        "mode": mode,
        "until_at": until_at.isoformat() if until_at else None,
        "set_at": now.isoformat(),
        "set_by": actor_id,
    }
    conversation.metadata_ = metadata
    conversation.status = ConversationStatus.snoozed


@dataclass(frozen=True)
class UpdateStatusResult:
    kind: Literal[
        "forbidden",
        "invalid_status",
        "invalid_transition",
        "not_found",
        "updated",
    ]
    detail: str | None = None


def update_conversation_status(
    db: Session,
    *,
    conversation_id: str,
    new_status: str,
    actor_id: str | None = None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> UpdateStatusResult:
    try:
        if (roles is not None or scopes is not None) and not can_update_conversation_status(roles, scopes):
            return UpdateStatusResult(kind="forbidden", detail="Not authorized")
        status_enum = ConversationStatus(new_status)
        conversation = conversation_service.Conversations.get(db, conversation_id)
        previous_status = conversation.status
        check = validate_transition(conversation.status, status_enum)
        if not check.allowed:
            return UpdateStatusResult(kind="invalid_transition", detail=check.reason)
        conversation_service.Conversations.update(
            db,
            conversation_id,
            ConversationUpdate(status=status_enum),
        )
        conversation = None
        if db is not None:
            conversation = db.get(Conversation, coerce_uuid(conversation_id))
        if conversation and status_enum != ConversationStatus.snoozed:
            _clear_snooze_metadata(conversation)
            db.commit()
        # Populate resolved_at / resolution_time_seconds
        if conversation is None and db is not None:
            conversation = db.get(Conversation, coerce_uuid(conversation_id))
        if conversation:
            if status_enum == ConversationStatus.resolved:
                now = datetime.now(UTC)
                conversation.resolved_at = now
                created = conversation.created_at
                if created is not None and created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                conversation.resolution_time_seconds = int(
                    (now - created).total_seconds()
                ) if created else 0
                db.commit()
            elif previous_status == ConversationStatus.resolved:
                conversation.resolved_at = None
                conversation.resolution_time_seconds = None
                db.commit()
        inbox_cache.invalidate_inbox_list()
        log_conversation_action(
            db,
            action="update_status",
            conversation_id=conversation_id,
            actor_id=actor_id,
            metadata={"status": status_enum.value},
        )
        if (
            db is not None
            and status_enum == ConversationStatus.resolved
            and previous_status != ConversationStatus.resolved
        ):
            # CSAT enqueue is best-effort and should never block resolve operations.
            queue_for_resolved_conversation(
                db,
                conversation_id=conversation_id,
                author_id=actor_id,
            )
        return UpdateStatusResult(kind="updated")
    except ValueError:
        return UpdateStatusResult(kind="invalid_status")
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            return UpdateStatusResult(kind="not_found")
        raise


@dataclass(frozen=True)
class UpdatePriorityResult:
    kind: Literal["not_found", "invalid_priority", "updated"]
    detail: str | None = None


def update_conversation_priority(
    db: Session,
    *,
    conversation_id: str,
    priority: str,
    actor_id: str | None = None,
) -> UpdatePriorityResult:
    """Update conversation priority via the service layer."""
    try:
        priority_enum = ConversationPriority(priority)
    except ValueError:
        return UpdatePriorityResult(kind="invalid_priority", detail=f"Invalid priority: {priority}")

    conv = db.get(Conversation, coerce_uuid(conversation_id))
    if not conv:
        return UpdatePriorityResult(kind="not_found")

    conv.priority = priority_enum
    db.commit()
    inbox_cache.invalidate_inbox_list()
    log_conversation_action(
        db,
        conversation_id=conversation_id,
        action="priority_changed",
        actor_id=actor_id,
        metadata={"priority": priority},
    )
    return UpdatePriorityResult(kind="updated")


@dataclass(frozen=True)
class ToggleMuteResult:
    kind: Literal["not_found", "updated"]
    is_muted: bool = False


def toggle_conversation_mute(
    db: Session,
    *,
    conversation_id: str,
    actor_id: str | None = None,
) -> ToggleMuteResult:
    """Toggle mute on a conversation via the service layer."""
    conv = db.get(Conversation, coerce_uuid(conversation_id))
    if not conv:
        return ToggleMuteResult(kind="not_found")

    conv.is_muted = not conv.is_muted
    db.commit()
    inbox_cache.invalidate_inbox_list()
    log_conversation_action(
        db,
        conversation_id=conversation_id,
        action="mute_toggled",
        actor_id=actor_id,
        metadata={"is_muted": conv.is_muted},
    )
    return ToggleMuteResult(kind="updated", is_muted=conv.is_muted)


@dataclass(frozen=True)
class SnoozeConversationResult:
    kind: Literal["not_found", "invalid_option", "invalid_until", "updated"]
    detail: str | None = None
    until_at: datetime | None = None


def snooze_conversation(
    db: Session,
    *,
    conversation_id: str,
    preset: str,
    until_at_raw: str | None = None,
    actor_id: str | None = None,
) -> SnoozeConversationResult:
    conv = db.get(Conversation, coerce_uuid(conversation_id))
    if not conv:
        return SnoozeConversationResult(kind="not_found")

    now = datetime.now(UTC)
    option = (preset or "").strip().lower()
    until_at: datetime | None = None
    mode = option
    if option == "1h":
        until_at = now + timedelta(hours=1)
    elif option == "tomorrow":
        tomorrow = (now + timedelta(days=1)).date()
        until_at = datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC).replace(hour=9)
    elif option == "next_week":
        days_until_monday = (7 - now.weekday()) or 7
        next_monday = (now + timedelta(days=days_until_monday)).date()
        until_at = datetime.combine(next_monday, datetime.min.time(), tzinfo=UTC).replace(hour=9)
    elif option == "next_reply":
        until_at = None
    elif option == "custom":
        until_at = _parse_snooze_until(until_at_raw)
        if not until_at:
            return SnoozeConversationResult(kind="invalid_until", detail="Invalid custom snooze time")
        if until_at <= now:
            return SnoozeConversationResult(kind="invalid_until", detail="Custom snooze time must be in the future")
    else:
        return SnoozeConversationResult(kind="invalid_option", detail=f"Unsupported snooze preset: {preset}")

    _apply_snooze(
        conv,
        now=now,
        mode=mode,
        until_at=until_at,
        actor_id=actor_id,
    )
    db.commit()
    inbox_cache.invalidate_inbox_list()
    log_conversation_action(
        db,
        conversation_id=conversation_id,
        action="snoozed",
        actor_id=actor_id,
        metadata={"preset": option, "until_at": until_at.isoformat() if until_at else None},
    )
    return SnoozeConversationResult(kind="updated", until_at=until_at)


def reopen_due_snoozed_conversations(db: Session, *, now: datetime | None = None) -> int:
    timestamp = now or datetime.now(UTC)
    changed = 0
    candidates = db.query(Conversation).filter(Conversation.status == ConversationStatus.snoozed).all()
    for conversation in candidates:
        snooze = _extract_snooze(conversation)
        if not snooze:
            continue
        until_at = _parse_snooze_until(snooze.get("until_at"))
        if not until_at or until_at > timestamp:
            continue
        conversation.status = ConversationStatus.open
        _clear_snooze_metadata(conversation)
        changed += 1
    if changed:
        db.commit()
        inbox_cache.invalidate_inbox_list()
    return changed


def reopen_snooze_on_next_reply(conversation: Conversation) -> bool:
    if conversation.status != ConversationStatus.snoozed:
        return False
    snooze = _extract_snooze(conversation)
    if not snooze:
        return False
    if str(snooze.get("mode") or "").strip().lower() != "next_reply":
        return False
    conversation.status = ConversationStatus.open
    _clear_snooze_metadata(conversation)
    return True
