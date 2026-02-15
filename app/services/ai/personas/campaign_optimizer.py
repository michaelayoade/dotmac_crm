from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain
from app.services.ai.personas._base import ContextQualityResult, OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry


def _quality(db: Session, params: dict[str, Any]) -> ContextQualityResult:
    from app.services.data_quality.scoring import score_campaign_quality

    r = score_campaign_quality(db, params.get("campaign_id", ""))
    return ContextQualityResult(score=r.score, field_scores=r.field_scores, missing_fields=r.missing_fields)


def _classify_severity(output: dict[str, Any]) -> str:
    risk = str(output.get("deliverability_risk") or "").strip().lower()
    if risk in {"critical", "high"}:
        return "warning"
    if risk in {"medium"}:
        return "suggestion"
    return "info"


_OUTPUT_SCHEMA = OutputSchema(
    fields=(
        OutputField("deliverability_risk", "string", "none, low, medium, high"),
        OutputField("content_issues", "list[string]", "0-6 content issues hurting performance", required=False),
        OutputField("targeting_issues", "list[string]", "0-6 targeting/segment issues", required=False),
        OutputField("recommended_changes", "list[string]", "3-8 specific changes to improve results"),
        OutputField("success_metrics_to_watch", "list[string]", "2-6 metrics to monitor (open, click, fail, etc.)"),
        OutputField("title", "string", "Short title for the insight (max 12 words)"),
        OutputField("summary", "string", "2-4 sentence optimization summary"),
        OutputField("confidence", "float", "0.0-1.0 confidence", required=False),
    )
)


_SYSTEM = """You are a CRM campaign optimizer.

Analyze campaign configuration and performance counters to recommend improvements.

Rules:
- Be specific and operational.
- If counters indicate failures, propose likely causes and verification steps.
- Never output PII; reference recipients only as aggregates.
- Return ONLY valid JSON. No markdown.

{output_instructions}
"""


def _context(db: Session, params: dict[str, Any]) -> str:
    from app.services.ai.context_builders.campaigns import gather_campaign_context

    return gather_campaign_context(db, params)


persona_registry.register(
    PersonaSpec(
        key="campaign_optimizer",
        name="Campaign Optimizer",
        domain=InsightDomain.campaigns,
        description="Optimizes campaigns based on configuration and outcome counters.",
        system_prompt=_SYSTEM,
        output_schema=_OUTPUT_SCHEMA,
        context_builder=_context,
        default_max_tokens=1200,
        supports_scheduled=True,
        default_schedule_seconds=6 * 3600,
        severity_classifier=_classify_severity,
        setting_key="intelligence_campaign_optimizer_enabled",
        insight_ttl_hours=72,
        context_quality_scorer=_quality,
        min_context_quality=0.40,
        skip_on_low_quality=True,
    )
)
