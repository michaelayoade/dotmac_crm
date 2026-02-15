from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.tickets import Ticket
from app.services.ai.engine import intelligence_engine
from app.services.audit_helpers import log_audit_event
from app.services.common import coerce_uuid


@dataclass(frozen=True)
class TicketAISummary:
    summary: str
    next_actions: list[str]
    meta: dict


def summarize_ticket(
    db: Session,
    *,
    request,
    ticket_id: str,
    actor_person_id: str | None,
    max_comments: int = 10,
    max_chars_per_comment: int = 600,
) -> TicketAISummary:
    ticket = db.get(Ticket, coerce_uuid(ticket_id))
    if not ticket:
        raise ValueError("Ticket not found")

    # Delegate to ticket analyst persona so the output is structured + persisted.
    insight = intelligence_engine.invoke(
        db,
        persona_key="ticket_analyst",
        params={
            "ticket_id": ticket_id,
            "max_comments": max_comments,
            "max_chars_per_comment": max_chars_per_comment,
        },
        entity_type="ticket",
        entity_id=str(ticket.id),
        trigger="on_demand",
        triggered_by_person_id=actor_person_id,
    )
    output = insight.structured_output or {}
    summary = str(output.get("summary") or "").strip()
    next_actions = output.get("recommended_actions") or output.get("recommendations") or []
    if not isinstance(next_actions, list):
        next_actions = []
    next_actions = [str(x).strip() for x in next_actions if str(x).strip()]
    if not summary:
        summary = "No summary generated."

    log_audit_event(
        db,
        request,
        action="ai_ticket_summary",
        entity_type="ticket",
        entity_id=str(ticket.id),
        actor_id=actor_person_id,
        metadata={
            "insight_id": str(insight.id),
            "llm_provider": insight.llm_provider,
            "llm_model": insight.llm_model,
            "llm_endpoint": insight.llm_endpoint,
        },
        status_code=200,
        is_success=True,
    )

    return TicketAISummary(
        summary=summary[:2000] or "No summary generated.",
        next_actions=next_actions[:10],
        meta={"provider": insight.llm_provider, "model": insight.llm_model, "insight_id": str(insight.id)},
    )
