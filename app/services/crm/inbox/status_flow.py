"""Conversation status flow rules."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.crm.enums import ConversationStatus

RESOLVED_CLOSING_METADATA_KEY = "resolved_closing_message"

_ALLOWED_TRANSITIONS: dict[ConversationStatus, set[ConversationStatus]] = {
    ConversationStatus.open: {
        ConversationStatus.open,
        ConversationStatus.pending,
        ConversationStatus.snoozed,
        ConversationStatus.resolved_to_ticket,
        ConversationStatus.resolved,
    },
    ConversationStatus.pending: {
        ConversationStatus.pending,
        ConversationStatus.open,
        ConversationStatus.snoozed,
        ConversationStatus.resolved_to_ticket,
        ConversationStatus.resolved,
    },
    ConversationStatus.snoozed: {
        ConversationStatus.snoozed,
        ConversationStatus.open,
        ConversationStatus.pending,
        ConversationStatus.resolved_to_ticket,
        ConversationStatus.resolved,
    },
    ConversationStatus.resolved_to_ticket: {
        ConversationStatus.resolved_to_ticket,
        ConversationStatus.open,
    },
    ConversationStatus.resolved: {
        ConversationStatus.resolved,
    },
}


@dataclass(frozen=True)
class TransitionCheck:
    allowed: bool
    reason: str | None = None


def is_transition_allowed(current: ConversationStatus, target: ConversationStatus) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, set())


def validate_transition(current: ConversationStatus, target: ConversationStatus) -> TransitionCheck:
    if is_transition_allowed(current, target):
        return TransitionCheck(allowed=True)
    return TransitionCheck(
        allowed=False,
        reason=f"Transition {current.value} -> {target.value} is not allowed",
    )


def apply_status_transition(conversation, target: ConversationStatus) -> TransitionCheck:
    current = conversation.status or ConversationStatus.open
    if not isinstance(current, ConversationStatus):
        current = ConversationStatus(str(current))
    check = validate_transition(current, target)
    if check.allowed:
        conversation.status = target
        if current == ConversationStatus.resolved_to_ticket and target == ConversationStatus.open:
            metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
            if RESOLVED_CLOSING_METADATA_KEY in metadata:
                metadata = dict(metadata)
                metadata.pop(RESOLVED_CLOSING_METADATA_KEY, None)
                conversation.metadata_ = metadata
    return check
