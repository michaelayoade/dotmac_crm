from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain
from app.services.ai.personas._base import ContextQualityResult, OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry


def _quality(db: Session, params: dict[str, Any]) -> ContextQualityResult:
    from app.services.data_quality.scoring import score_subscriber_quality

    r = score_subscriber_quality(db, params.get("subscriber_id", ""))
    return ContextQualityResult(score=r.score, field_scores=r.field_scores, missing_fields=r.missing_fields)


def _classify_severity(output: dict[str, Any]) -> str:
    churn = str(output.get("churn_risk") or "").strip().lower()
    if churn in {"high"}:
        return "warning"
    if churn in {"medium"}:
        return "suggestion"
    return "info"


_OUTPUT_SCHEMA = OutputSchema(
    fields=(
        OutputField("churn_risk", "string", "low, medium, high"),
        OutputField("sentiment", "string", "positive, neutral, frustrated, angry, unknown"),
        OutputField("key_issues", "list[string]", "1-6 key issues affecting the customer relationship"),
        OutputField("proactive_actions", "list[string]", "3-8 specific proactive actions to reduce churn"),
        OutputField("upsell_opportunities", "list[string]", "0-6 opportunities (if appropriate)", required=False),
        OutputField("title", "string", "Short title for the insight (max 12 words)"),
        OutputField("summary", "string", "2-4 sentence customer success summary"),
        OutputField("confidence", "float", "0.0-1.0 confidence", required=False),
    )
)


_SYSTEM = """You are a customer success analyst for a telecom operator.

Assess the customer's recent support history and recommend proactive actions.

Rules:
- Use only information in the context.
- Do not include PII in outputs.
- If context is thin, be conservative and lower confidence.
- Return ONLY valid JSON. No markdown.

{output_instructions}
"""


def _context(db: Session, params: dict[str, Any]) -> str:
    from app.services.ai.context_builders.customers import gather_customer_context

    return gather_customer_context(db, params)


persona_registry.register(
    PersonaSpec(
        key="customer_success",
        name="Customer Success",
        domain=InsightDomain.customer_success,
        description="Flags churn risk and recommends proactive customer actions.",
        system_prompt=_SYSTEM,
        output_schema=_OUTPUT_SCHEMA,
        context_builder=_context,
        default_max_tokens=1200,
        supports_scheduled=True,
        default_schedule_seconds=12 * 3600,
        severity_classifier=_classify_severity,
        setting_key="intelligence_customer_success_enabled",
        insight_ttl_hours=72,
        context_quality_scorer=_quality,
        min_context_quality=0.20,
        skip_on_low_quality=False,
    )
)
