from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.services.ai.engine import intelligence_engine
from app.services.audit_helpers import log_audit_event
from app.services.common import coerce_uuid


@dataclass(frozen=True)
class CRMReplySuggestion:
    draft: str
    meta: dict


def suggest_conversation_reply(
    db: Session,
    *,
    request,
    conversation_id: str,
    actor_person_id: str | None,
    endpoint: str = "primary",
    max_messages: int = 12,
    max_chars_per_message: int = 600,
) -> CRMReplySuggestion:
    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        raise ValueError("Conversation not found")

    # Delegate to intelligence engine persona for consistent persistence + audit.
    # Keep endpoint arg for backward compatibility (persona controls endpoint selection).
    insight = intelligence_engine.invoke(
        db,
        persona_key="inbox_analyst",
        params={
            "conversation_id": conversation_id,
            "max_messages": max_messages,
            "max_chars_per_message": max_chars_per_message,
        },
        entity_type="conversation",
        entity_id=str(conversation.id),
        trigger="on_demand",
        triggered_by_person_id=actor_person_id,
    )
    output = insight.structured_output or {}
    draft = str(output.get("draft") or "").strip()
    if not draft:
        draft = "I couldn't generate a reply draft right now. Please try again."

    log_audit_event(
        db,
        request,
        action="ai_suggest_reply",
        entity_type="crm_conversation",
        entity_id=str(conversation.id),
        actor_id=actor_person_id,
        metadata={
            "insight_id": str(insight.id),
            "llm_provider": insight.llm_provider,
            "llm_model": insight.llm_model,
            "llm_endpoint": insight.llm_endpoint,
            "requested_endpoint": endpoint,
        },
        status_code=200,
        is_success=True,
    )

    return CRMReplySuggestion(
        draft=draft,
        meta={"provider": insight.llm_provider, "model": insight.llm_model, "insight_id": str(insight.id)},
    )
