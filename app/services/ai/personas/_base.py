from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain

# ---------------------------------------------------------------------------
# Data quality types (used by both data_quality module and AI engine)
# ---------------------------------------------------------------------------


@dataclass
class ContextQualityResult:
    """Result of evaluating context data completeness for a persona."""

    score: float  # 0.0 (empty) to 1.0 (fully populated)
    field_scores: dict[str, float] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)

    @property
    def sufficient(self) -> bool:
        """Whether the score meets a reasonable minimum (0.3)."""
        return self.score >= 0.3


def _default_quality_scorer(_db: Session, _params: dict[str, Any]) -> ContextQualityResult:
    """Fallback scorer: always returns sufficient. Used when no scorer is defined."""
    return ContextQualityResult(score=1.0)


# ---------------------------------------------------------------------------
# Output schema types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutputField:
    name: str
    type: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class OutputSchema:
    fields: tuple[OutputField, ...]

    def required_keys(self) -> list[str]:
        return [f.name for f in self.fields if f.required]

    def to_instruction(self) -> str:
        lines = ["Return a JSON object with these keys:"]
        for f in self.fields:
            req = "required" if f.required else "optional"
            lines.append(f'  - "{f.name}" ({f.type}, {req}): {f.description}')
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persona specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonaSpec:
    key: str
    name: str
    domain: InsightDomain
    description: str
    system_prompt: str
    output_schema: OutputSchema
    context_builder: Callable[[Session, dict[str, Any]], str]
    default_max_tokens: int = 1200
    default_endpoint: str = "primary"  # primary|secondary (ai gateway endpoint name)
    supports_scheduled: bool = False
    default_schedule_seconds: int = 0
    setting_key: str | None = None
    insight_ttl_hours: int = 72
    severity_classifier: Callable[[dict[str, Any]], str] | None = None

    # Data readiness gate
    context_quality_scorer: Callable[[Session, dict[str, Any]], ContextQualityResult] = _default_quality_scorer
    min_context_quality: float = 0.0  # 0.0 = no gate (always proceed)
    skip_on_low_quality: bool = True  # if True, skip LLM; if False, proceed but tag
