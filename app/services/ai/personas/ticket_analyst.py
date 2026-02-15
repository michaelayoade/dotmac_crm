from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain
from app.services.ai.personas._base import ContextQualityResult, OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry


def _quality(db: Session, params: dict[str, Any]) -> ContextQualityResult:
    from app.services.data_quality.scoring import score_ticket_quality

    r = score_ticket_quality(db, params.get("ticket_id", ""))
    return ContextQualityResult(score=r.score, field_scores=r.field_scores, missing_fields=r.missing_fields)


def _classify_severity(output: dict[str, Any]) -> str:
    score = output.get("priority_score", 50)
    try:
        value = int(score)
    except Exception:
        value = 50
    if value >= 90:
        return "critical"
    if value >= 70:
        return "warning"
    if value >= 50:
        return "suggestion"
    return "info"


_OUTPUT_SCHEMA = OutputSchema(
    fields=(
        OutputField("priority_score", "integer", "0-100 score indicating true priority"),
        OutputField("category", "string", "Issue category: billing, technical, service, complaint, other"),
        OutputField("sentiment", "string", "Customer sentiment: positive, neutral, frustrated, angry"),
        OutputField("escalation_risk", "string", "Risk of escalation: low, medium, high"),
        OutputField("title", "string", "Short title for the insight (max 12 words)"),
        OutputField("summary", "string", "2-4 sentence summary of the core issue"),
        OutputField("recommended_actions", "list[string]", "3-5 specific next actions"),
        OutputField("sla_risk", "string", "SLA breach risk: none, low, medium, high", required=False),
        OutputField("confidence", "float", "0.0-1.0 confidence in the analysis", required=False),
    )
)


_SYSTEM = """You are an expert support ticket analyst for a telecommunications company.

Analyze the ticket data and produce structured triage intelligence.

Rules:
- Be factual and specific. Reference actual data.
- Priority score: 0 (trivial) to 100 (critical outage or severe impact).
- Recommended actions must be concrete and actionable.
- Return ONLY valid JSON. No markdown.

{output_instructions}
"""


def _context(db: Session, params: dict[str, Any]) -> str:
    from app.services.ai.context_builders.tickets import gather_ticket_context

    return gather_ticket_context(db, params)


persona_registry.register(
    PersonaSpec(
        key="ticket_analyst",
        name="Ticket Analyst",
        domain=InsightDomain.tickets,
        description="Analyzes tickets for triage, priority, and next actions.",
        system_prompt=_SYSTEM,
        output_schema=_OUTPUT_SCHEMA,
        context_builder=_context,
        default_max_tokens=1200,
        supports_scheduled=True,
        default_schedule_seconds=3600,
        severity_classifier=_classify_severity,
        setting_key="intelligence_ticket_analyst_enabled",
        insight_ttl_hours=72,
        context_quality_scorer=_quality,
        min_context_quality=0.30,
        skip_on_low_quality=True,
    )
)
