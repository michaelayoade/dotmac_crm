from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain
from app.services.ai.personas._base import ContextQualityResult, OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry


def _quality(db: Session, params: dict[str, Any]) -> ContextQualityResult:
    from app.services.data_quality.scoring import score_conversation_quality

    r = score_conversation_quality(db, params.get("conversation_id", ""))
    return ContextQualityResult(score=r.score, field_scores=r.field_scores, missing_fields=r.missing_fields)


_OUTPUT_SCHEMA = OutputSchema(
    fields=(
        OutputField("draft", "string", "Reply draft text (<= 120 words)"),
        OutputField("tone", "string", "Tone used: professional, friendly, firm, apologetic"),
        OutputField("clarifying_questions", "list[string]", "0-3 questions to ask if needed", required=False),
        OutputField("title", "string", "Short title for the insight (max 12 words)"),
        OutputField("summary", "string", "1-2 sentence summary of what the draft does"),
        OutputField("confidence", "float", "0.0-1.0 confidence", required=False),
    )
)


_SYSTEM = """You are an expert CRM support agent.

Write a helpful, concise reply draft to the customer.

Rules:
- Do not mention internal systems.
- Ask clarifying questions only if necessary.
- Keep it under 120 words.
- Return ONLY valid JSON. No markdown.

{output_instructions}
"""


def _context(db: Session, params: dict[str, Any]) -> str:
    from app.services.ai.context_builders.inbox import gather_inbox_context

    return gather_inbox_context(db, params)


persona_registry.register(
    PersonaSpec(
        key="inbox_analyst",
        name="Inbox Analyst",
        domain=InsightDomain.inbox,
        description="Generates reply drafts and quick context summaries for conversations.",
        system_prompt=_SYSTEM,
        output_schema=_OUTPUT_SCHEMA,
        context_builder=_context,
        default_max_tokens=600,
        supports_scheduled=False,
        severity_classifier=None,
        setting_key="intelligence_inbox_analyst_enabled",
        insight_ttl_hours=24,
        context_quality_scorer=_quality,
        min_context_quality=0.35,
        skip_on_low_quality=True,
    )
)
