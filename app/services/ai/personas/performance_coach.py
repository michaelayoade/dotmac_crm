from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain
from app.services.ai.personas._base import ContextQualityResult, OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry


def _quality(db: Session, params: dict[str, Any]) -> ContextQualityResult:
    from app.services.data_quality.scoring import score_performance_snapshot_quality

    r = score_performance_snapshot_quality(db, params.get("person_id", ""), params.get("period_start"))
    return ContextQualityResult(score=r.score, field_scores=r.field_scores, missing_fields=r.missing_fields)


_OUTPUT_SCHEMA = OutputSchema(
    fields=(
        OutputField("strengths", "list[string]", "2-8 strengths (specific behaviors)"),
        OutputField("improvements", "list[string]", "2-8 improvement areas (specific behaviors)"),
        OutputField("recommendations", "list[string]", "3-8 coaching recommendations"),
        OutputField("callouts", "list[string]", "0-8 notable callouts from evidence samples", required=False),
        OutputField("title", "string", "Short title for the insight (max 12 words)"),
        OutputField("summary", "string", "2-5 sentence coaching summary"),
        OutputField("confidence", "float", "0.0-1.0 confidence", required=False),
    )
)


_SYSTEM = """You are a performance coach for a telecom operations team.

Given performance scores and redacted evidence samples, write a structured coaching review.

Rules:
- Be constructive, specific, and action-oriented.
- Do not invent facts; only reference evidence in the context.
- Return ONLY valid JSON. No markdown.

{output_instructions}
"""


def _context(db: Session, params: dict[str, Any]) -> str:
    from app.services.ai.context_builders.performance import gather_performance_context

    return gather_performance_context(db, params)


persona_registry.register(
    PersonaSpec(
        key="performance_coach",
        name="Performance Coach",
        domain=InsightDomain.performance,
        description="Generates coaching insights from performance snapshots and evidence samples.",
        system_prompt=_SYSTEM,
        output_schema=_OUTPUT_SCHEMA,
        context_builder=_context,
        default_max_tokens=1600,
        supports_scheduled=True,
        default_schedule_seconds=24 * 3600,
        severity_classifier=None,
        setting_key="intelligence_performance_coach_enabled",
        insight_ttl_hours=240,
        context_quality_scorer=_quality,
        min_context_quality=0.35,
        skip_on_low_quality=True,
    )
)
