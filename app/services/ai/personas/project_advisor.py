from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain
from app.services.ai.personas._base import OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry


def _classify_severity(output: dict[str, Any]) -> str:
    # health_score: 0 (failing) -> 100 (healthy)
    score = output.get("health_score", 70)
    try:
        value = int(score)
    except Exception:
        value = 70
    if value <= 30:
        return "critical"
    if value <= 55:
        return "warning"
    if value <= 75:
        return "suggestion"
    return "info"


_OUTPUT_SCHEMA = OutputSchema(
    fields=(
        OutputField("health_score", "integer", "0-100 overall project health score"),
        OutputField("status_summary", "string", "One sentence: current state in plain language"),
        OutputField("top_risks", "list[string]", "2-6 concrete risks (schedule, scope, dependencies)"),
        OutputField("blockers", "list[string]", "0-6 blockers that prevent progress", required=False),
        OutputField("recommended_actions", "list[string]", "3-6 specific next actions"),
        OutputField("title", "string", "Short title for the insight (max 12 words)"),
        OutputField("summary", "string", "2-4 sentence summary of what's happening and why it matters"),
        OutputField("confidence", "float", "0.0-1.0 confidence", required=False),
    )
)


_SYSTEM = """You are an expert project advisor for a telecommunications field operations team.

Analyze the project data and produce concise, structured guidance.

Rules:
- Use only facts present in the context.
- Risks and actions must be concrete (not generic).
- If information is missing, say so in status_summary and reduce confidence.
- Return ONLY valid JSON. No markdown.

{output_instructions}
"""


def _context(db: Session, params: dict[str, Any]) -> str:
    from app.services.ai.context_builders.projects import gather_project_context

    return gather_project_context(db, params)


persona_registry.register(
    PersonaSpec(
        key="project_advisor",
        name="Project Advisor",
        domain=InsightDomain.projects,
        description="Assesses project health, risks, and concrete next actions.",
        system_prompt=_SYSTEM,
        output_schema=_OUTPUT_SCHEMA,
        context_builder=_context,
        default_max_tokens=1200,
        supports_scheduled=True,
        default_schedule_seconds=6 * 3600,
        severity_classifier=_classify_severity,
        setting_key="intelligence_project_advisor_enabled",
        insight_ttl_hours=72,
    )
)
