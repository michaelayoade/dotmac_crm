from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.team import CrmAgent

AI_INTAKE_METADATA_KEY = "ai_intake"
RESOLVED_CLOSING_METADATA_KEY = "resolved_closing_message"


def ensure_aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
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
    metadata_value = getattr(conversation, "metadata_", None)
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
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
    assignment_at = ensure_aware(getattr(assignment, "assigned_at", None)) if assignment else None
    assignment_created_at = (
        ensure_aware(getattr(assignment, "created_at", None)) if assignment and assignment_at is None else None
    )
    candidates = [
        assignment_at,
        assignment_created_at,
        ensure_aware(getattr(conversation, "first_assigned_at", None)),
        parse_metadata_timestamp(state.get("human_assigned_at")),
        parse_metadata_timestamp(state.get("assigned_at")),
        parse_metadata_timestamp(state.get("agent_assigned_at")),
        parse_metadata_timestamp(state.get("handoff_sent_at")),
        parse_metadata_timestamp(state.get("resolved_at")),
    ]
    valid = [candidate for candidate in candidates if candidate is not None]
    if valid:
        return max(valid)
    return ensure_aware(getattr(conversation, "created_at", None))


def effective_first_response_start_at(
    conversation: Conversation,
    *,
    assignment: ConversationAssignment | None = None,
    first_inbound_at: datetime | None = None,
    response_at: datetime | None = None,
) -> datetime | None:
    """Start time for first response metrics.

    A human agent should be timed from when the customer was waiting on that
    agent. Use handoff/assignment when it exists before the reply, but fall
    back to the customer wait start if historical assignment metadata was
    recorded after the human reply.
    """
    customer_wait_start = ensure_aware(first_inbound_at) or ensure_aware(getattr(conversation, "created_at", None))
    response_time = ensure_aware(response_at)
    responsibility_start = effective_handoff_at(conversation, assignment=assignment)
    if responsibility_start and response_time and responsibility_start <= response_time:
        if customer_wait_start and customer_wait_start > responsibility_start:
            return customer_wait_start
        return responsibility_start
    return customer_wait_start


def resolved_closing_message_ids(conversation: Conversation) -> set[str]:
    metadata_value = getattr(conversation, "metadata_", None)
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    closing = metadata.get(RESOLVED_CLOSING_METADATA_KEY)
    if not isinstance(closing, dict):
        return set()
    ids: set[str] = set()
    message_id = closing.get("message_id")
    if message_id:
        ids.add(str(message_id))
    ticket_handoffs = closing.get("ticket_handoffs")
    if isinstance(ticket_handoffs, dict):
        for state in ticket_handoffs.values():
            if isinstance(state, dict) and state.get("message_id"):
                ids.add(str(state["message_id"]))
    return ids


def is_resolved_closing_message(message: Any, *, conversation: Conversation | None = None) -> bool:
    if conversation is not None and str(getattr(message, "id", "")) in resolved_closing_message_ids(conversation):
        return True
    metadata_value = getattr(message, "metadata_", None)
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    if metadata.get("resolved_closing_message") or metadata.get("resolved_closing_generated"):
        return True
    body = (getattr(message, "body", None) or "").strip().lower()
    if not body:
        return False
    return (
        body.startswith("thanks for chatting with us today")
        or body.startswith("glad we could get this sorted")
        or body.startswith("your request has been successfully resolved")
        or "follow us for updates" in body
    )


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
