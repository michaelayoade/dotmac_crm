from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain
from app.services.ai.personas._base import OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry


def _classify_severity(output: dict[str, Any]) -> str:
    risk = str(output.get("schedule_risk") or "").strip().lower()
    if risk in {"high"}:
        return "warning"
    if risk in {"medium"}:
        return "suggestion"
    return "info"


_OUTPUT_SCHEMA = OutputSchema(
    fields=(
        OutputField("schedule_risk", "string", "none, low, medium, high"),
        OutputField("assignment_gaps", "list[string]", "0-6 gaps preventing dispatch", required=False),
        OutputField("recommended_actions", "list[string]", "3-8 actions to get job completed on time"),
        OutputField("title", "string", "Short title for the insight (max 12 words)"),
        OutputField("summary", "string", "2-4 sentence dispatch plan summary"),
        OutputField("confidence", "float", "0.0-1.0 confidence", required=False),
    )
)


_SYSTEM = """You are a dispatch planner for a telecom field service team.

Analyze the work order context and propose a practical plan.

Rules:
- Do not invent technicians or schedules. Use only provided facts.
- If unassigned, suggest what skills/role are needed and next operational steps.
- Return ONLY valid JSON. No markdown.

{output_instructions}
"""


def _context(db: Session, params: dict[str, Any]) -> str:
    from app.services.ai.context_builders.dispatch import gather_dispatch_context

    return gather_dispatch_context(db, params)


persona_registry.register(
    PersonaSpec(
        key="dispatch_planner",
        name="Dispatch Planner",
        domain=InsightDomain.dispatch,
        description="Assesses work orders for dispatch readiness and next operational steps.",
        system_prompt=_SYSTEM,
        output_schema=_OUTPUT_SCHEMA,
        context_builder=_context,
        default_max_tokens=1200,
        supports_scheduled=True,
        default_schedule_seconds=2 * 3600,
        severity_classifier=_classify_severity,
        setting_key="intelligence_dispatch_planner_enabled",
        insight_ttl_hours=48,
    )
)
