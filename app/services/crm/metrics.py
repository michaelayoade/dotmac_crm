from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.team import CrmAgent

AI_INTAKE_METADATA_KEY = "ai_intake"


def ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def parse_metadata_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ensure_aware(parsed)


def ai_intake_state(conversation: Conversation) -> dict[str, Any]:
    metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
    state = metadata.get(AI_INTAKE_METADATA_KEY)
    return state if isinstance(state, dict) else {}


def effective_handoff_at(
    conversation: Conversation,
    *,
    assignment: ConversationAssignment | None = None,
) -> datetime | None:
    """Start time for human-agent performance metrics.

    For AI-assisted chats, this uses the latest known handoff/assignment marker so
    AI intake time is excluded. For ordinary chats, assignment time is preferred
    and conversation creation is the fallback.
    """
    state = ai_intake_state(conversation)
    assignment_at = ensure_aware(assignment.assigned_at) if assignment else None
    assignment_created_at = ensure_aware(assignment.created_at) if assignment and assignment_at is None else None
    candidates = [
        assignment_at,
        assignment_created_at,
        ensure_aware(conversation.first_assigned_at),
        parse_metadata_timestamp(state.get("human_assigned_at")),
        parse_metadata_timestamp(state.get("assigned_at")),
        parse_metadata_timestamp(state.get("agent_assigned_at")),
        parse_metadata_timestamp(state.get("handoff_sent_at")),
        parse_metadata_timestamp(state.get("resolved_at")),
    ]
    valid = [candidate for candidate in candidates if candidate is not None]
    if valid:
        return max(valid)
    return ensure_aware(conversation.created_at)


def active_assignment_for_agent(
    db: Session,
    *,
    conversation_id,
    agent_id,
) -> ConversationAssignment | None:
    return (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation_id)
        .filter(ConversationAssignment.agent_id == agent_id)
        .filter(ConversationAssignment.is_active.is_(True))
        .order_by(
            ConversationAssignment.assigned_at.desc().nullslast(),
            ConversationAssignment.created_at.desc(),
        )
        .first()
    )


def active_agent_assignment_for_conversation(
    db: Session,
    *,
    conversation_id,
) -> ConversationAssignment | None:
    return (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation_id)
        .filter(ConversationAssignment.agent_id.isnot(None))
        .filter(ConversationAssignment.is_active.is_(True))
        .order_by(
            ConversationAssignment.assigned_at.desc().nullslast(),
            ConversationAssignment.created_at.desc(),
        )
        .first()
    )


def agent_for_person(db: Session, person_id) -> CrmAgent | None:
    return db.query(CrmAgent).filter(CrmAgent.person_id == person_id).filter(CrmAgent.is_active.is_(True)).first()
