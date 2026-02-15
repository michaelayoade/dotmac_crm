from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain
from app.services.ai.personas._base import ContextQualityResult, OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry


def _quality(db: Session, params: dict[str, Any]) -> ContextQualityResult:
    from app.services.data_quality.scoring import score_vendor_quote_quality

    r = score_vendor_quote_quality(db, params.get("quote_id", ""))
    return ContextQualityResult(score=r.score, field_scores=r.field_scores, missing_fields=r.missing_fields)


def _classify_severity(output: dict[str, Any]) -> str:
    decision = str(output.get("recommended_decision") or "").strip().lower()
    if decision in {"reject"}:
        return "warning"
    if decision in {"revise", "negotiate"}:
        return "suggestion"
    return "info"


_OUTPUT_SCHEMA = OutputSchema(
    fields=(
        OutputField("recommended_decision", "string", "approve, revise, negotiate, reject"),
        OutputField("price_risks", "list[string]", "0-6 pricing risks or anomalies", required=False),
        OutputField("scope_gaps", "list[string]", "0-8 scope gaps or missing line items", required=False),
        OutputField("negotiation_points", "list[string]", "2-8 negotiation points"),
        OutputField("recommended_actions", "list[string]", "3-8 next actions (approve/revise workflow)"),
        OutputField("title", "string", "Short title for the insight (max 12 words)"),
        OutputField("summary", "string", "2-4 sentence quote assessment summary"),
        OutputField("confidence", "float", "0.0-1.0 confidence", required=False),
    )
)


_SYSTEM = """You are a vendor quote analyst for telecom infrastructure projects.

Analyze the quote and project context and propose a clear decision and next steps.

Rules:
- Be specific: reference concrete line items and numbers present in context.
- If context is missing, note it and lower confidence.
- Return ONLY valid JSON. No markdown.

{output_instructions}
"""


def _context(db: Session, params: dict[str, Any]) -> str:
    from app.services.ai.context_builders.vendors import gather_vendor_context

    return gather_vendor_context(db, params)


persona_registry.register(
    PersonaSpec(
        key="vendor_analyst",
        name="Vendor Analyst",
        domain=InsightDomain.vendors,
        description="Evaluates vendor quotes for scope/price risks and recommends next steps.",
        system_prompt=_SYSTEM,
        output_schema=_OUTPUT_SCHEMA,
        context_builder=_context,
        default_max_tokens=1400,
        supports_scheduled=True,
        default_schedule_seconds=6 * 3600,
        severity_classifier=_classify_severity,
        setting_key="intelligence_vendor_analyst_enabled",
        insight_ttl_hours=120,
        context_quality_scorer=_quality,
        min_context_quality=0.25,
        skip_on_low_quality=False,
    )
)
