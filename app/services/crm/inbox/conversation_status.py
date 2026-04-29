"""Conversation status helpers for CRM inbox."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationPriority, ConversationStatus, MessageDirection
from app.models.domain_settings import SettingDomain
from app.schemas.crm.conversation import ConversationUpdate
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox.audit import log_conversation_action
from app.services.crm.inbox.csat import queue_for_resolved_conversation
from app.services.crm.inbox.permissions import can_update_conversation_status
from app.services.crm.inbox.status_flow import validate_transition
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)
SNOOZE_METADATA_KEY = "snooze"
RESOLVED_CLOSING_METADATA_KEY = "resolved_closing_message"
FEEDBACK_URL = "https://crm.dotmac.io/s/t/FP3WThiNslFHUnPHR4FObkOeeoWwe8rMc9CIftn2-tc"
RESOLVED_CLOSING_EMAIL_SUBJECT = "Support Request Resolved"
RESOLVED_CLOSING_CLAIM_TTL_SECONDS = 600
SUPPORTED_RESOLVE_CLOSING_CHANNELS: set[ChannelType] = {
    ChannelType.whatsapp,
    ChannelType.email,
}

WHATSAPP_SOCIAL_TEMPLATE = (
    "Glad we could get this sorted 😊\n\n"
    "Stay connected with us for updates and tips:\n"
    "📸 Instagram: @dotmac_ng\n"
    "📘 Facebook: Dotmac Fiber\n"
    "🌐 www.dotmac.ng\n\n"
    "If you need anything else, just message us anytime 👍"
)
WHATSAPP_FEEDBACK_TEMPLATE = (
    "Glad we could get this sorted 😊\n\n"
    "We'd really appreciate your feedback when you have a moment:\n"
    f"{FEEDBACK_URL}\n\n"
    "If you need anything else, we're here 👍"
)
EMAIL_SOCIAL_TEMPLATE = (
    "Hello,\n\n"
    "We're glad your request has been successfully resolved.\n\n"
    "Stay connected with us for updates and service information:\n\n"
    "Instagram: @dotmac_ng\n"
    "Facebook: Dotmac Fiber\n"
    "Website: www.dotmac.ng\n\n"
    "If you need further assistance, feel free to reach out anytime.\n\n"
    "Kind regards,\n"
    "DOTMAC Support Team"
)
EMAIL_FEEDBACK_TEMPLATE = (
    "Hello,\n\n"
    "We're glad your request has been successfully resolved.\n\n"
    "We would appreciate your feedback on your experience:\n"
    f"{FEEDBACK_URL}\n\n"
    "If you require any further assistance, please don't hesitate to contact us.\n\n"
    "Kind regards,\n"
    "DOTMAC Support Team"
)


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


def _extract_resolved_closing_message(conversation: Conversation) -> dict | None:
    if not isinstance(conversation.metadata_, dict):
        return None
    raw = conversation.metadata_.get(RESOLVED_CLOSING_METADATA_KEY)
    return raw if isinstance(raw, dict) else None


def _parse_iso_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_stale_in_progress(value: object, *, now: datetime) -> bool:
    claimed_at = _parse_iso_utc(value)
    if not claimed_at:
        return True
    return (now - claimed_at).total_seconds() > RESOLVED_CLOSING_CLAIM_TTL_SECONDS


def _should_send_resolved_closing_message(
    *,
    conversation: Conversation,
    status_enum: ConversationStatus,
    previous_status: ConversationStatus | None,
) -> bool:
    resolved_closing = _extract_resolved_closing_message(conversation)
    now = datetime.now(UTC)
    in_progress_value = resolved_closing.get("send_in_progress_at") if isinstance(resolved_closing, dict) else None
    return (
        status_enum == ConversationStatus.resolved
        and previous_status != ConversationStatus.resolved
        and not (isinstance(resolved_closing, dict) and resolved_closing.get("sent_at"))
        and (
            not isinstance(resolved_closing, dict)
            or not resolved_closing.get("send_in_progress_at")
            or _is_stale_in_progress(in_progress_value, now=now)
        )
    )


def _claim_resolved_closing_message_send(
    db: Session,
    *,
    conversation: Conversation,
    channel_type: ChannelType,
    variant: str,
) -> bool:
    """Claim send ownership so parallel retries do not duplicate outbound sends.

    Uses a row lock so concurrent resolve requests cannot both claim.
    """
    now = datetime.now(UTC)
    locked_conversation: Conversation | None = None
    try:
        locked_conversation = (
            db.query(Conversation).filter(Conversation.id == conversation.id).with_for_update().one_or_none()
        )
    except Exception:
        # Fallback for environments where FOR UPDATE is unavailable.
        locked_conversation = db.get(Conversation, conversation.id)
    if not locked_conversation:
        return False

    metadata = _metadata_dict(locked_conversation)
    existing = metadata.get(RESOLVED_CLOSING_METADATA_KEY)
    closing = dict(existing) if isinstance(existing, dict) else {}
    if closing.get("sent_at"):
        return False
    in_progress_value = closing.get("send_in_progress_at")
    if in_progress_value and not _is_stale_in_progress(in_progress_value, now=now):
        return False

    closing["send_in_progress_at"] = now.isoformat()
    closing["variant"] = variant
    closing["channel_type"] = channel_type.value
    metadata[RESOLVED_CLOSING_METADATA_KEY] = closing
    locked_conversation.metadata_ = metadata
    db.commit()
    return True


def _persist_resolved_closing_message_metadata(
    db: Session,
    *,
    conversation: Conversation,
    variant: str,
    sent: bool,
    message_id: str | None = None,
    channel_type: ChannelType | None = None,
    error_detail: str | None = None,
) -> None:
    metadata = _metadata_dict(conversation)
    existing = metadata.get(RESOLVED_CLOSING_METADATA_KEY)
    closing = dict(existing) if isinstance(existing, dict) else {}
    now_iso = datetime.now(UTC).isoformat()
    if sent:
        closing["sent_at"] = now_iso
        closing["message_id"] = message_id
        closing["channel_type"] = channel_type.value if channel_type else None
        closing["variant"] = variant
        closing.pop("last_error", None)
        closing.pop("last_error_at", None)
    else:
        closing["last_error"] = error_detail or "send_failed"
        closing["last_error_at"] = now_iso
        closing["variant"] = variant
        if channel_type:
            closing["channel_type"] = channel_type.value
    closing.pop("send_in_progress_at", None)
    metadata[RESOLVED_CLOSING_METADATA_KEY] = closing
    conversation.metadata_ = metadata
    db.commit()


def _select_resolved_closing_variant(*, random_value: float | None = None) -> Literal["social", "feedback"]:
    sample = random.random() if random_value is None else random_value
    return "social" if sample < 0.70 else "feedback"


def _resolve_latest_channel_type(db: Session, conversation_id: str) -> ChannelType | None:
    conversation_uuid = coerce_uuid(conversation_id)
    latest_inbound_channel_type = (
        db.query(Message.channel_type)
        .filter(Message.conversation_id == conversation_uuid)
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(Message.created_at.desc())
        .limit(1)
        .scalar()
    )
    if latest_inbound_channel_type:
        return latest_inbound_channel_type
    return (
        db.query(Message.channel_type)
        .filter(Message.conversation_id == conversation_uuid)
        .order_by(Message.created_at.desc())
        .limit(1)
        .scalar()
    )


def _build_resolved_closing_message(
    db: Session,
    *,
    channel_type: ChannelType,
    variant: Literal["social", "feedback"],
) -> tuple[str | None, str]:
    if variant == "social":
        configured = resolve_value(db, SettingDomain.notification, "crm_inbox_resolved_social_outro_message")
        configured_text = str(configured).strip() if configured is not None else ""
        if configured_text:
            return RESOLVED_CLOSING_EMAIL_SUBJECT if channel_type == ChannelType.email else None, configured_text
    if channel_type == ChannelType.email:
        if variant == "social":
            return RESOLVED_CLOSING_EMAIL_SUBJECT, EMAIL_SOCIAL_TEMPLATE
        return RESOLVED_CLOSING_EMAIL_SUBJECT, EMAIL_FEEDBACK_TEMPLATE
    if variant == "social":
        return None, WHATSAPP_SOCIAL_TEMPLATE
    return None, WHATSAPP_FEEDBACK_TEMPLATE


def _send_resolved_closing_message(
    db: Session,
    *,
    conversation_id: str,
    channel_type: ChannelType,
    variant: Literal["social", "feedback"],
    actor_id: str | None,
) -> tuple[bool, str | None, str | None, str | None]:
    from app.services.crm.inbox.admin_ui import send_conversation_message

    subject, message_text = _build_resolved_closing_message(
        db,
        channel_type=channel_type,
        variant=variant,
    )

    try:
        result = send_conversation_message(
            db=db,
            conversation_id=conversation_id,
            message_text=message_text,
            subject=subject,
            attachments_json=None,
            idempotency_key=f"resolved-closing:{conversation_id}:{channel_type.value}:{variant}",
            reply_to_message_id=None,
            template_id=None,
            scheduled_at=None,
            author_id=actor_id,
            trace_id=f"resolved-closing:{conversation_id}",
            roles=None,
            scopes=None,
        )
    except Exception as exc:
        return False, None, None, str(exc)

    if result.kind != "success":
        return False, None, None, result.error_detail or result.kind

    if not result.message:
        return True, None, None, None

    message_id = str(result.message.id) if getattr(result.message, "id", None) else None
    channel_type_raw = getattr(result.message, "channel_type", None)
    message_channel_type = getattr(channel_type_raw, "value", None) or (
        str(channel_type_raw) if channel_type_raw is not None else None
    )
    return True, message_id, message_channel_type, None


def _should_queue_csat_for_channel(channel_type: ChannelType | None) -> bool:
    # Keep CSAT auto-send on resolve only for in-app widget conversations.
    return channel_type == ChannelType.chat_widget


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
                conversation.resolution_time_seconds = int((now - created).total_seconds()) if created else 0
                db.commit()
            elif previous_status == ConversationStatus.resolved:
                conversation.resolved_at = None
                conversation.resolution_time_seconds = None
                db.commit()
        if db is not None:
            from app.services.crm.inbox.summaries import recompute_conversation_summary

            recompute_conversation_summary(db, conversation_id)
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
            resolved_channel_type = _resolve_latest_channel_type(db, conversation_id)
            if _should_queue_csat_for_channel(resolved_channel_type):
                # CSAT enqueue is best-effort and should never block resolve operations.
                queue_for_resolved_conversation(
                    db,
                    conversation_id=conversation_id,
                    author_id=actor_id,
                )
            if (
                conversation
                and resolved_channel_type in SUPPORTED_RESOLVE_CLOSING_CHANNELS
                and _should_send_resolved_closing_message(
                    conversation=conversation,
                    status_enum=status_enum,
                    previous_status=previous_status,
                )
            ):
                variant = _select_resolved_closing_variant()
                claimed = _claim_resolved_closing_message_send(
                    db,
                    conversation=conversation,
                    channel_type=resolved_channel_type,
                    variant=variant,
                )
                if claimed:
                    sent, message_id, _, error_detail = _send_resolved_closing_message(
                        db,
                        conversation_id=conversation_id,
                        channel_type=resolved_channel_type,
                        variant=variant,
                        actor_id=actor_id,
                    )
                    try:
                        _persist_resolved_closing_message_metadata(
                            db,
                            conversation=conversation,
                            variant=variant,
                            sent=sent,
                            message_id=message_id,
                            channel_type=resolved_channel_type,
                            error_detail=error_detail,
                        )
                    except Exception:  # nosec B110 - metadata persistence should not block resolve flow
                        logger.exception(
                            "failed_to_persist_resolved_closing_message_metadata conversation_id=%s",
                            conversation_id,
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
    from app.services.crm.inbox.summaries import recompute_conversation_summary

    recompute_conversation_summary(db, conversation_id)
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
