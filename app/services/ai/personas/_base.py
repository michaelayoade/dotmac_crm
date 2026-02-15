from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import InsightDomain


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
