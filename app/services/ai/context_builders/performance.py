from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.team import CrmAgent
from app.models.performance import AgentPerformanceSnapshot
from app.models.person import Person
from app.models.tickets import Ticket
from app.models.workforce import WorkOrder
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def _parse_dt(value: object | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            # Accept ISO; force UTC if missing tz for stability.
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def gather_performance_context(db: Session, params: dict[str, Any]) -> str:
    """
    Context builder for agent performance coaching.

    Required params:
      - person_id
      - period_start (ISO)
      - period_end (ISO)
    """
    person_id = params.get("person_id")
    if not person_id:
        raise ValueError("person_id is required")
    period_start = _parse_dt(params.get("period_start"))
    period_end = _parse_dt(params.get("period_end"))
    if not period_start or not period_end:
        raise ValueError("period_start and period_end are required (ISO datetimes)")

    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise ValueError("Person not found")

    snapshot = (
        db.query(AgentPerformanceSnapshot)
        .filter(
            AgentPerformanceSnapshot.person_id == coerce_uuid(person_id),
            AgentPerformanceSnapshot.score_period_start == period_start,
            AgentPerformanceSnapshot.score_period_end == period_end,
        )
        .first()
    )
    if not snapshot:
        raise ValueError("No performance snapshot found for requested period")

    max_samples = min(int(params.get("max_samples", 8)), 20)
    max_chars = int(params.get("max_chars", 650))

    display_name = person.display_name or f"{person.first_name or ''} {person.last_name or ''}".strip() or "Agent"

    lines: list[str] = [
        f"Agent: {redact_text(display_name, max_chars=120)}",
        f"Period start: {period_start.isoformat()}",
        f"Period end: {period_end.isoformat()}",
        f"Composite score: {float(snapshot.composite_score) if snapshot.composite_score is not None else 'unknown'}",
        f"Domain scores JSON: {snapshot.domain_scores_json}",
    ]

    samples: list[str] = []

    tickets = (
        db.query(Ticket)
        .filter(
            Ticket.assigned_to_person_id == coerce_uuid(person_id),
            Ticket.created_at >= period_start,
            Ticket.created_at <= period_end,
        )
        .order_by(Ticket.updated_at.desc())
        .limit(3)
        .all()
    )
    for t in tickets:
        summary = redact_text(f"{t.title or ''}. {t.description or ''}", max_chars=max_chars)
        samples.append(f"Ticket {t.number or str(t.id)[:8]} ({t.status.value}): {summary}")

    agent = (
        db.query(CrmAgent).filter(CrmAgent.person_id == coerce_uuid(person_id), CrmAgent.is_active.is_(True)).first()
    )
    if agent:
        convo_ids = (
            db.query(ConversationAssignment.conversation_id)
            .filter(ConversationAssignment.agent_id == agent.id, ConversationAssignment.is_active.is_(True))
            .all()
        )
        convo_ids = [row[0] for row in convo_ids]
        if convo_ids:
            conversations = (
                db.query(Conversation)
                .filter(
                    Conversation.id.in_(convo_ids),
                    Conversation.created_at >= period_start,
                    Conversation.created_at <= period_end,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(3)
                .all()
            )
            for c in conversations:
                first = (
                    db.query(Message).filter(Message.conversation_id == c.id).order_by(Message.created_at.asc()).first()
                )
                preview = redact_text(f"{c.subject or ''}. {(first.body if first else '') or ''}", max_chars=max_chars)
                samples.append(f"Conversation {str(c.id)[:8]} ({c.status.value}): {preview}")

    work_orders = (
        db.query(WorkOrder)
        .filter(
            WorkOrder.assigned_to_person_id == coerce_uuid(person_id),
            WorkOrder.created_at >= period_start,
            WorkOrder.created_at <= period_end,
        )
        .order_by(WorkOrder.updated_at.desc())
        .limit(2)
        .all()
    )
    for wo in work_orders:
        preview = redact_text(f"{wo.title}. {wo.description or ''}", max_chars=max_chars)
        samples.append(f"Work order {str(wo.id)[:8]} ({wo.status.value}): {preview}")

    if samples:
        lines.append("Evidence samples (redacted):")
        for s in samples[:max_samples]:
            lines.append(f"  - {s}")

    return "\n".join([line for line in lines if line.strip()])
