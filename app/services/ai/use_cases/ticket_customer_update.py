from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.tickets import Ticket, TicketComment
from app.services.ai.engine import intelligence_engine
from app.services.audit_helpers import log_audit_event
from app.services.common import coerce_uuid


@dataclass(frozen=True)
class TicketCustomerUpdateDraft:
    update_message: str
    meta: dict


def draft_customer_ticket_update(
    db: Session,
    *,
    request,
    ticket_id: str,
    comment_id: str,
    actor_person_id: str | None,
) -> TicketCustomerUpdateDraft:
    ticket = db.get(Ticket, coerce_uuid(ticket_id))
    if not ticket:
        raise ValueError("Ticket not found")
    comment = db.get(TicketComment, coerce_uuid(comment_id))
    if not comment:
        raise ValueError("Ticket comment not found")

    insight = intelligence_engine.invoke(
        db,
        persona_key="ticket_customer_update_writer",
        params={"ticket_id": ticket_id, "comment_id": comment_id},
        entity_type="ticket_comment",
        entity_id=str(comment.id),
        trigger="event",
        triggered_by_person_id=actor_person_id,
    )
    output = insight.structured_output or {}
    update_message = str(output.get("update_message") or output.get("summary") or "").strip()
    if not update_message:
        raise ValueError("No customer update message generated")

    log_audit_event(
        db,
        request,
        action="ai_ticket_customer_update",
        entity_type="ticket",
        entity_id=str(ticket.id),
        actor_id=actor_person_id,
        metadata={
            "comment_id": str(comment.id),
            "insight_id": str(insight.id),
            "llm_provider": insight.llm_provider,
            "llm_model": insight.llm_model,
            "llm_endpoint": insight.llm_endpoint,
        },
        status_code=200,
        is_success=True,
    )

    return TicketCustomerUpdateDraft(
        update_message=update_message[:4000],
        meta={
            "provider": insight.llm_provider,
            "model": insight.llm_model,
            "insight_id": str(insight.id),
        },
    )
