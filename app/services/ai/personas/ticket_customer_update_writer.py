from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain
from app.models.person import Person
from app.models.tickets import Ticket, TicketComment
from app.services.ai.personas._base import ContextQualityResult, OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def _quality(db: Session, params: dict[str, object]) -> ContextQualityResult:
    score = 0.0
    missing: list[str] = []
    ticket_id = params.get("ticket_id")
    comment_id = params.get("comment_id")
    ticket = db.get(Ticket, coerce_uuid(ticket_id)) if ticket_id else None
    comment = db.get(TicketComment, coerce_uuid(comment_id)) if comment_id else None
    if ticket:
        score += 0.5
    else:
        missing.append("ticket")
    if comment and (comment.body or "").strip():
        score += 0.5
    else:
        missing.append("comment")
    return ContextQualityResult(
        score=score,
        field_scores={"ticket": 0.5 if ticket else 0.0, "comment": 0.5 if comment else 0.0},
        missing_fields=missing,
    )


_OUTPUT_SCHEMA = OutputSchema(
    fields=(
        OutputField("title", "string", "Short title for the insight"),
        OutputField("summary", "string", "Short summary of the customer-facing rewrite"),
        OutputField("update_message", "string", "Customer-facing update text without greeting or sign-off"),
        OutputField("confidence", "float", "0.0-1.0 confidence in the rewrite", required=False),
    )
)


_SYSTEM = """You rewrite technician ticket updates into customer-facing support updates.

Rules:
- Preserve only facts that are present in the ticket or comment.
- Remove internal jargon, internal-only notes, or anything that should not be sent to the customer.
- Do not invent timelines, promises, diagnoses, or completed actions.
- Keep the update concise, professional, and easy to understand.
- The `update_message` should be plain text only, 1-4 short paragraphs, with no greeting or sign-off.
- Return ONLY valid JSON. No markdown.

{output_instructions}
"""


def _context(db: Session, params: dict[str, object]) -> str:
    ticket = db.get(Ticket, coerce_uuid(params.get("ticket_id")))
    comment = db.get(TicketComment, coerce_uuid(params.get("comment_id")))
    if not ticket:
        raise ValueError("Ticket not found")
    if not comment:
        raise ValueError("Ticket comment not found")

    customer_name = "Customer"
    if ticket.customer_person_id:
        customer = db.get(Person, ticket.customer_person_id)
        if customer:
            customer_name = (
                customer.display_name or f"{customer.first_name} {customer.last_name}".strip() or customer_name
            )

    author_name = "Technician"
    if comment.author_person_id:
        author = db.get(Person, comment.author_person_id)
        if author:
            author_name = author.display_name or f"{author.first_name} {author.last_name}".strip() or author_name

    return "\n".join(
        [
            f"Ticket ID: {ticket.number or ticket.id}",
            f"Ticket title: {redact_text(ticket.title or '', max_chars=160)}",
            f"Ticket status: {ticket.status.value if ticket.status else 'unknown'}",
            f"Customer name: {redact_text(customer_name, max_chars=80)}",
            f"Comment author: {redact_text(author_name, max_chars=80)}",
            f"Comment visibility: {'internal' if comment.is_internal else 'public'}",
            f"Original technician update: {redact_text(comment.body or '', max_chars=1200)}",
        ]
    )


persona_registry.register(
    PersonaSpec(
        key="ticket_customer_update_writer",
        name="Ticket Customer Update Writer",
        domain=InsightDomain.tickets,
        description="Rewrites technician ticket comments into customer-facing updates.",
        system_prompt=_SYSTEM,
        output_schema=_OUTPUT_SCHEMA,
        context_builder=_context,
        default_max_tokens=500,
        supports_scheduled=False,
        insight_ttl_hours=168,
        context_quality_scorer=_quality,
        min_context_quality=0.5,
        skip_on_low_quality=True,
    )
)
