# Intelligence Engine -- Implementation Plan

## 1. Design Overview

The Intelligence Engine is a structured system of AI specialist "personas" that analyze, summarize, recommend, and act on domain data. It layers on top of the existing AI gateway infrastructure (`app/services/ai/gateway.py`) and follows the established service-layer / manager-singleton / Celery-task patterns.

**Core architectural idea:** Each persona is a declarative configuration object (not a class hierarchy) registered in a flat registry. An execution engine (`IntelligenceEngine`) orchestrates the pipeline: check prerequisites, gather domain context, build prompts, call the LLM via `ai_gateway`, parse structured output, persist as an `AIInsight` row, and optionally trigger notifications.

This repo already has standalone AI use-cases (`app/services/ai/use_cases/*`) and a dual-endpoint gateway (primary + secondary) to support:
- Primary: hosted OpenAI-compatible provider (example: DeepSeek)
- Secondary: self-hosted OpenAI-compatible server on another machine (example: Llama)

Those use-cases can optionally be migrated into this framework as personas later, keeping compatibility wrappers for existing call sites.

## 2. File Structure

```
app/
  models/
    ai_insight.py                          # NEW -- AIInsight, AIInsightStatus, InsightDomain, InsightSeverity
  schemas/
    ai_insight.py                          # NEW -- Pydantic read/create schemas
  services/
    ai/
      __init__.py                          # MODIFY -- re-export new symbols
      client.py                            # UNCHANGED
      gateway.py                           # UNCHANGED
      redaction.py                         # UNCHANGED
      engine.py                            # NEW -- IntelligenceEngine orchestrator
      personas/                            # NEW -- persona definitions package
        __init__.py                        #   registry + base types
        _registry.py                       #   PersonaRegistry singleton
        _base.py                           #   PersonaSpec, OutputSchema, ContextSpec dataclasses
        ticket_analyst.py                  #   TicketAnalyst persona
        project_advisor.py                 #   ProjectAdvisor persona
        inbox_analyst.py                   #   InboxAnalyst persona (absorbs crm_reply)
        campaign_optimizer.py              #   CampaignOptimizer persona
        dispatch_planner.py                #   DispatchPlanner persona
        vendor_analyst.py                  #   VendorAnalyst persona
        performance_coach.py               #   PerformanceCoach persona (absorbs performance_review)
        customer_success.py                #   CustomerSuccess persona
      context_builders/                    # NEW -- domain data gatherers
        __init__.py
        tickets.py                         #   gather_ticket_context()
        projects.py                        #   gather_project_context()
        inbox.py                           #   gather_inbox_context()
        campaigns.py                       #   gather_campaign_context()
        dispatch.py                        #   gather_dispatch_context()
        vendors.py                         #   gather_vendor_context()
        performance.py                     #   gather_performance_context()
        customers.py                       #   gather_customer_context()
      output_parsers.py                    # NEW -- JSON/structured output parsing utilities
      use_cases/                           # KEEP -- thin wrappers for backward compatibility
        crm_reply.py                       #   delegates to engine + inbox_analyst persona
        ticket_summary.py                  #   delegates to engine + ticket_analyst persona
      prompts/
        performance_review.py              # KEEP -- used by performance_coach persona
  tasks/
    intelligence.py                        # NEW -- Celery tasks for scheduled analysis
  api/
    ai.py                                  # MODIFY -- add insight endpoints
  web/
    admin/
      intelligence.py                      # NEW -- admin web routes for insights dashboard
templates/
  admin/
    intelligence/                          # NEW -- insight dashboard templates
      index.html
      _insights_table.html
      detail.html
alembic/
  versions/
    xxxx_add_ai_insights_table.py          # NEW migration
tests/
  test_intelligence_engine.py              # NEW
  test_ai_personas.py                      # NEW
```

## 3. Model Definition -- `app/models/ai_insight.py`

```python
import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer,
    Numeric, String, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class InsightDomain(enum.Enum):
    tickets = "tickets"
    projects = "projects"
    inbox = "inbox"
    campaigns = "campaigns"
    dispatch = "dispatch"
    vendors = "vendors"
    performance = "performance"
    customer_success = "customer_success"


class InsightSeverity(enum.Enum):
    info = "info"
    suggestion = "suggestion"
    warning = "warning"
    critical = "critical"


class AIInsightStatus(enum.Enum):
    pending = "pending"          # queued for generation
    completed = "completed"      # successfully generated
    failed = "failed"            # LLM call failed
    acknowledged = "acknowledged"  # user has seen/dismissed it
    actioned = "actioned"        # user took recommended action
    expired = "expired"          # TTL passed, no longer relevant


class AIInsight(Base):
    __tablename__ = "ai_insights"
    __table_args__ = (
        Index("ix_ai_insight_domain_status", "domain", "status"),
        Index("ix_ai_insight_entity", "entity_type", "entity_id"),
        Index("ix_ai_insight_persona", "persona_key"),
        Index("ix_ai_insight_created", "created_at"),
        Index("ix_ai_insight_severity", "severity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    persona_key: Mapped[str] = mapped_column(String(80), nullable=False)
    domain: Mapped[InsightDomain] = mapped_column(Enum(InsightDomain), nullable=False)
    severity: Mapped[InsightSeverity] = mapped_column(
        Enum(InsightSeverity), default=InsightSeverity.info
    )
    status: Mapped[AIInsightStatus] = mapped_column(
        Enum(AIInsightStatus), default=AIInsightStatus.pending
    )

    # What entity this insight is about
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)  # "ticket", "project", "conversation", etc.
    entity_id: Mapped[str | None] = mapped_column(String(120))            # UUID of the entity, or None for aggregate

    # Structured output
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    structured_output: Mapped[dict | None] = mapped_column(JSON)  # typed JSON per persona
    confidence_score: Mapped[float | None] = mapped_column(Numeric(3, 2))  # 0.00-1.00
    recommendations: Mapped[list | None] = mapped_column(JSON)    # list of action items

    # LLM metadata
    llm_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="vllm")
    llm_model: Mapped[str] = mapped_column(String(100), nullable=False)
    llm_tokens_in: Mapped[int | None] = mapped_column(Integer)
    llm_tokens_out: Mapped[int | None] = mapped_column(Integer)
    llm_endpoint: Mapped[str | None] = mapped_column(String(20))   # "primary" | "secondary"
    generation_time_ms: Mapped[int | None] = mapped_column(Integer)

    # Trigger info
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)  # "on_demand", "scheduled", "event"
    triggered_by_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )

    # Lifecycle
    is_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    triggered_by = relationship("Person", foreign_keys=[triggered_by_person_id])
    acknowledged_by = relationship("Person", foreign_keys=[acknowledged_by_person_id])
```

**Key design decisions:**
- Single table for all insight types -- the `persona_key` + `domain` disambiguates. This avoids table-per-persona proliferation.
- `structured_output` is a JSON column whose schema is persona-dependent. Each persona defines what goes here.
- `entity_type` + `entity_id` is a polymorphic reference (same pattern as `AuditEvent`).
- `severity` enables UI filtering and notification triggering (critical insights can push notifications).
- `trigger` records whether it was on-demand, scheduled, or event-driven.

## 4. Persona Registry Design -- `app/services/ai/personas/`

### 4a. Base Types (`_base.py`)

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class OutputField:
    """Describes one field expected in the LLM's JSON output."""
    name: str
    type: str           # "string", "float", "list[string]", "dict", etc.
    description: str
    required: bool = True


@dataclass(frozen=True)
class OutputSchema:
    """Declarative definition of what the LLM should return."""
    fields: tuple[OutputField, ...]

    def to_instruction(self) -> str:
        """Generate the JSON schema instruction for the system prompt."""
        lines = ["Return a JSON object with these keys:"]
        for f in self.fields:
            req = "required" if f.required else "optional"
            lines.append(f'  - "{f.name}" ({f.type}, {req}): {f.description}')
        return "\n".join(lines)


@dataclass(frozen=True)
class PersonaSpec:
    """Complete definition of an AI specialist persona."""
    key: str                          # unique identifier, e.g. "ticket_analyst"
    name: str                         # display name, e.g. "Ticket Analyst"
    domain: str                       # maps to InsightDomain value
    description: str                  # what this persona does

    # Prompt components
    system_prompt: str                # the persona's identity and rules
    output_schema: OutputSchema       # expected structured output

    # Context gathering
    context_builder: Callable[[Session, dict[str, Any]], str]
    # ^^ function(db, params) -> formatted context string for the user prompt

    # Execution parameters
    default_max_tokens: int = 1500
    default_endpoint: str = "primary"    # "primary" or "secondary" (matches AI gateway endpoint names)
    temperature: float | None = None     # override gateway default if set

    # Token budget constraints
    max_context_chars: int = 8000        # truncate context if exceeds
    max_output_chars: int = 5000         # truncate output if exceeds

    # Scheduling
    supports_scheduled: bool = False     # can this run as a periodic background task?
    default_schedule_seconds: int = 0    # 0 = no schedule

    # Severity mapping (function that inspects output and returns severity)
    severity_classifier: Callable[[dict[str, Any]], str] | None = None

    # Feature flag setting key (in integration domain)
    setting_key: str | None = None       # e.g. "intelligence_ticket_analyst_enabled"

    # Insight TTL in hours (0 = never expires)
    insight_ttl_hours: int = 168         # default 7 days
```

### 4b. Registry (`_registry.py`)

```python
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class PersonaRegistry:
    """Registry for AI personas. Singleton."""

    def __init__(self) -> None:
        self._personas: dict[str, Any] = {}  # key -> PersonaSpec

    def register(self, spec) -> None:
        if spec.key in self._personas:
            logger.warning("Persona %s already registered, overwriting", spec.key)
        self._personas[spec.key] = spec

    def get(self, key: str):
        spec = self._personas.get(key)
        if not spec:
            raise ValueError(f"Unknown persona: {key}")
        return spec

    def list_all(self) -> list:
        return list(self._personas.values())

    def list_by_domain(self, domain: str) -> list:
        return [s for s in self._personas.values() if s.domain == domain]

    def keys(self) -> list[str]:
        return list(self._personas.keys())


persona_registry = PersonaRegistry()
```

### 4c. Persona Registration (`__init__.py`)

```python
from app.services.ai.personas._registry import persona_registry

# Import persona modules to trigger registration
from app.services.ai.personas import (  # noqa: F401
    ticket_analyst,
    project_advisor,
    inbox_analyst,
    campaign_optimizer,
    dispatch_planner,
    vendor_analyst,
    performance_coach,
    customer_success,
)

__all__ = ["persona_registry"]
```

### 4d. Example Persona -- `ticket_analyst.py`

```python
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.ai.personas._base import OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry


def _classify_severity(output: dict[str, Any]) -> str:
    score = output.get("priority_score", 50)
    if isinstance(score, (int, float)):
        if score >= 90:
            return "critical"
        if score >= 70:
            return "warning"
        if score >= 50:
            return "suggestion"
    return "info"


_output_schema = OutputSchema(fields=(
    OutputField("priority_score", "integer", "0-100 score indicating true priority"),
    OutputField("category", "string", "Issue category: billing, technical, service, complaint, other"),
    OutputField("sentiment", "string", "Customer sentiment: positive, neutral, frustrated, angry"),
    OutputField("escalation_risk", "string", "Risk of escalation: low, medium, high"),
    OutputField("summary", "string", "2-3 sentence summary of the core issue"),
    OutputField("root_cause_hypothesis", "string", "Best guess at root cause"),
    OutputField("recommended_actions", "list[string]", "3-5 specific next actions"),
    OutputField("suggested_assignee_type", "string", "Skill/team type best suited", required=False),
    OutputField("sla_risk", "string", "SLA breach risk: none, low, medium, high"),
))


_SYSTEM_PROMPT = """You are an expert support ticket analyst for a telecommunications/utilities company.

Analyze the ticket data and provide structured triage intelligence.

Rules:
- Be factual and specific. Reference actual data from the ticket.
- Priority score: 0 (trivial) to 100 (critical infrastructure down, many affected).
- Factor in: customer type, issue recurrence, SLA status, business impact.
- Recommended actions must be concrete and actionable, not generic advice.
- If data is insufficient for a field, provide your best estimate and note the uncertainty.
{output_instructions}
Return ONLY valid JSON. No markdown, no explanation outside the JSON."""


def _context_builder(db: Session, params: dict[str, Any]) -> str:
    """Lazy import to avoid circular dependencies."""
    from app.services.ai.context_builders.tickets import gather_ticket_context
    return gather_ticket_context(db, params)


_spec = PersonaSpec(
    key="ticket_analyst",
    name="Ticket Analyst",
    domain="tickets",
    description="Analyzes tickets for priority scoring, smart assignment, SLA prediction, escalation detection, and root cause analysis.",
    system_prompt=_SYSTEM_PROMPT,
    output_schema=_output_schema,
    context_builder=_context_builder,
    default_max_tokens=1200,
    supports_scheduled=True,
    default_schedule_seconds=3600,  # hourly scan of new/open tickets
    severity_classifier=_classify_severity,
    setting_key="intelligence_ticket_analyst_enabled",
    insight_ttl_hours=72,
)

persona_registry.register(_spec)
```

## 5. Context Builders -- `app/services/ai/context_builders/`

Each context builder is a standalone function that queries domain data and returns a formatted text string suitable for the LLM prompt. The pattern is consistent.

**Example: `tickets.py`**

```python
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.tickets import Ticket, TicketComment, TicketSlaEvent
from app.models.workflow import SlaBreach
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def gather_ticket_context(db: Session, params: dict[str, Any]) -> str:
    """Gather context for a single ticket analysis.

    params:
        ticket_id (str): required
        max_comments (int): optional, default 8
        max_chars_per_comment (int): optional, default 500
    """
    ticket_id = params.get("ticket_id")
    if not ticket_id:
        raise ValueError("ticket_id is required")

    ticket = db.get(Ticket, coerce_uuid(ticket_id))
    if not ticket:
        raise ValueError("Ticket not found")

    max_comments = min(params.get("max_comments", 8), 20)
    max_chars = params.get("max_chars_per_comment", 500)

    lines = [
        f"Ticket ID: {ticket.number or str(ticket.id)[:8]}",
        f"Title: {redact_text(ticket.title or '', max_chars=200)}",
        f"Status: {ticket.status.value}",
        f"Priority: {ticket.priority.value}",
        f"Channel: {ticket.channel.value}",
        f"Type: {ticket.ticket_type or 'unclassified'}",
        f"Created: {ticket.created_at.isoformat() if ticket.created_at else 'unknown'}",
        f"Description: {redact_text(ticket.description or '', max_chars=800)}",
    ]

    # Customer info
    if ticket.customer_person_id:
        customer = db.get(Person, ticket.customer_person_id)
        if customer:
            lines.append(f"Customer: {redact_text(customer.display_name or '', max_chars=100)}")

    # Assigned agent
    if ticket.assigned_to_person_id:
        assignee = db.get(Person, ticket.assigned_to_person_id)
        if assignee:
            lines.append(f"Assigned to: {redact_text(assignee.display_name or '', max_chars=100)}")
    else:
        lines.append("Assigned to: UNASSIGNED")

    # SLA events
    sla_events = (
        db.query(TicketSlaEvent)
        .filter(TicketSlaEvent.ticket_id == ticket.id)
        .order_by(TicketSlaEvent.created_at.desc())
        .limit(3)
        .all()
    )
    if sla_events:
        lines.append("SLA Events:")
        for ev in sla_events:
            lines.append(f"  - {ev.event_type}: {ev.created_at.isoformat() if ev.created_at else 'unknown'}")

    # Comments
    comments = (
        db.query(TicketComment)
        .filter(TicketComment.ticket_id == ticket.id)
        .order_by(TicketComment.created_at.desc())
        .limit(max(1, max_comments))
        .all()
    )
    comments = list(reversed(comments))
    if comments:
        lines.append("Recent comments:")
        for c in comments:
            prefix = "internal" if c.is_internal else "public"
            body = redact_text(c.body or "", max_chars=max_chars)
            if body:
                lines.append(f"  [{prefix}] {body}")

    return "\n".join(lines)
```

Each domain builder follows the same signature: `(db: Session, params: dict[str, Any]) -> str`.

## 6. Execution Engine -- `app/services/ai/engine.py`

```python
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_insight import AIInsight, AIInsightStatus, InsightDomain, InsightSeverity
from app.services.ai.gateway import AIEndpoint, ai_gateway
from app.services.ai.output_parsers import parse_json_output
from app.services.ai.personas._base import PersonaSpec
from app.services.ai.personas._registry import persona_registry
from app.services.ai.redaction import redact_text
from app.services.audit_helpers import log_audit_event
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


class IntelligenceEngine:
    """Orchestrates persona-based AI analysis.

    Pipeline:
        1. Resolve persona from registry
        2. Check if AI is enabled + persona-specific feature flag
        3. Gather domain context via persona's context_builder
        4. Build system + user prompts
        5. Call LLM via ai_gateway
        6. Parse structured output
        7. Classify severity
        8. Persist as AIInsight
        9. Return insight
    """

    def invoke(
        self,
        db: Session,
        *,
        persona_key: str,
        params: dict[str, Any],
        entity_type: str,
        entity_id: str | None = None,
        trigger: str = "on_demand",
        triggered_by_person_id: str | None = None,
        endpoint: AIEndpoint | None = None,
        max_tokens: int | None = None,
        request: Any | None = None,
    ) -> AIInsight:
        spec = persona_registry.get(persona_key)
        self._check_enabled(db, spec)

        # Gather context
        context_text = spec.context_builder(db, params)
        if len(context_text) > spec.max_context_chars:
            context_text = context_text[:spec.max_context_chars] + "\n[...truncated]"

        # Build prompts
        output_instructions = spec.output_schema.to_instruction()
        system = spec.system_prompt.format(output_instructions=output_instructions)
        user_prompt = context_text

        # Call LLM
        effective_endpoint = endpoint or spec.default_endpoint
        effective_max_tokens = max_tokens or spec.default_max_tokens

        start_ms = time.monotonic_ns() // 1_000_000
        try:
            ai_result, routing_meta = ai_gateway.generate_with_fallback(
                db,
                primary=effective_endpoint,
                fallback="secondary" if effective_endpoint == "primary" else "primary",
                system=system,
                prompt=user_prompt,
                max_tokens=effective_max_tokens,
            )
            elapsed_ms = int((time.monotonic_ns() // 1_000_000) - start_ms)

            # Parse
            parsed = parse_json_output(ai_result.content, spec.output_schema)

            # Classify severity
            severity_value = "info"
            if spec.severity_classifier:
                severity_value = spec.severity_classifier(parsed)

            # Build title from parsed output
            title = self._build_title(spec, parsed, params)

            insight = AIInsight(
                persona_key=spec.key,
                domain=InsightDomain(spec.domain),
                severity=InsightSeverity(severity_value),
                status=AIInsightStatus.completed,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id else None,
                title=title[:300],
                summary=str(parsed.get("summary", ""))[:5000] or "Analysis completed.",
                structured_output=parsed,
                confidence_score=self._extract_confidence(parsed),
                recommendations=parsed.get("recommended_actions") or parsed.get("recommendations"),
                llm_provider=ai_result.provider,
                llm_model=ai_result.model,
                llm_tokens_in=ai_result.tokens_in,
                llm_tokens_out=ai_result.tokens_out,
                llm_endpoint=routing_meta.get("endpoint"),
                generation_time_ms=elapsed_ms,
                trigger=trigger,
                triggered_by_person_id=coerce_uuid(triggered_by_person_id),
                expires_at=self._compute_expiry(spec),
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic_ns() // 1_000_000) - start_ms)
            logger.warning("Intelligence engine failed for persona=%s: %s", persona_key, exc)
            insight = AIInsight(
                persona_key=spec.key,
                domain=InsightDomain(spec.domain),
                severity=InsightSeverity.info,
                status=AIInsightStatus.failed,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id else None,
                title=f"{spec.name} analysis failed",
                summary=f"Analysis failed: {exc}"[:2000],
                structured_output=None,
                llm_provider="unavailable",
                llm_model="unavailable",
                generation_time_ms=elapsed_ms,
                trigger=trigger,
                triggered_by_person_id=coerce_uuid(triggered_by_person_id),
                expires_at=self._compute_expiry(spec),
            )

        db.add(insight)
        db.commit()
        db.refresh(insight)

        # Audit
        log_audit_event(
            db,
            request=request,
            action=f"ai_insight_{spec.key}",
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=triggered_by_person_id,
            metadata={
                "persona": spec.key,
                "trigger": trigger,
                "llm_model": insight.llm_model,
                "tokens_in": insight.llm_tokens_in,
                "tokens_out": insight.llm_tokens_out,
                "generation_ms": insight.generation_time_ms,
                "status": insight.status.value,
            },
        )

        return insight

    def _check_enabled(self, db: Session, spec: PersonaSpec) -> None:
        if not ai_gateway.enabled(db):
            raise ValueError("AI features are disabled")
        if spec.setting_key:
            from app.services.settings_spec import resolve_value
            from app.models.domain_settings import SettingDomain
            val = resolve_value(db, SettingDomain.integration, spec.setting_key)
            if val is not None and not _truthy(val):
                raise ValueError(f"Persona {spec.key} is disabled via setting {spec.setting_key}")

    def _build_title(self, spec: PersonaSpec, parsed: dict, params: dict) -> str:
        # Use the summary's first sentence as a title, or fall back to spec.name
        summary = str(parsed.get("summary", ""))
        if summary:
            first_sentence = summary.split(".")[0].strip()
            if len(first_sentence) > 10:
                return first_sentence[:300]
        return f"{spec.name} Analysis"

    def _extract_confidence(self, parsed: dict) -> float | None:
        conf = parsed.get("confidence") or parsed.get("confidence_score")
        if isinstance(conf, (int, float)):
            return max(0.0, min(1.0, float(conf)))
        return None

    def _compute_expiry(self, spec: PersonaSpec) -> datetime | None:
        if spec.insight_ttl_hours <= 0:
            return None
        return datetime.now(UTC) + timedelta(hours=spec.insight_ttl_hours)


def _truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(val)


intelligence_engine = IntelligenceEngine()
```

## 7. Output Parser -- `app/services/ai/output_parsers.py`

```python
from __future__ import annotations

import json
import re
from typing import Any

from app.services.ai.personas._base import OutputSchema


def parse_json_output(raw: str, schema: OutputSchema) -> dict[str, Any]:
    """Parse LLM text output into a dict conforming to the OutputSchema.

    Handles:
    - Raw JSON
    - JSON wrapped in ```json ... ```
    - Partially valid JSON (attempts recovery)
    """
    text = raw.strip()

    # Strip markdown code fences
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        text = match.group(1).strip()

    # Try direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _validate_against_schema(data, schema)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            data = json.loads(text[brace_start : brace_end + 1])
            if isinstance(data, dict):
                return _validate_against_schema(data, schema)
        except json.JSONDecodeError:
            pass

    # Fallback: return raw text as summary
    return {"summary": text[:5000], "_parse_failed": True}


def _validate_against_schema(data: dict, schema: OutputSchema) -> dict[str, Any]:
    """Ensure required fields exist with correct types. Fills defaults for missing optional fields."""
    result = dict(data)
    for field in schema.fields:
        if field.name not in result:
            if field.required:
                result[field.name] = _default_for_type(field.type)
            else:
                result[field.name] = None
    return result


def _default_for_type(type_str: str) -> Any:
    if "list" in type_str:
        return []
    if "dict" in type_str:
        return {}
    if type_str in ("integer", "float", "number"):
        return 0
    if type_str == "boolean":
        return False
    return ""
```

## 8. Insight Manager Service

A lightweight CRUD manager for insights, following the same singleton pattern as other services.

```python
# In engine.py or a separate insights.py service file

class InsightManager:
    @staticmethod
    def list(
        db: Session,
        *,
        domain: str | None = None,
        persona_key: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[AIInsight]:
        query = db.query(AIInsight)
        if domain:
            query = query.filter(AIInsight.domain == InsightDomain(domain))
        if persona_key:
            query = query.filter(AIInsight.persona_key == persona_key)
        if entity_type:
            query = query.filter(AIInsight.entity_type == entity_type)
        if entity_id:
            query = query.filter(AIInsight.entity_id == entity_id)
        if status:
            query = query.filter(AIInsight.status == AIInsightStatus(status))
        if severity:
            query = query.filter(AIInsight.severity == InsightSeverity(severity))
        query = query.order_by(AIInsight.created_at.desc())
        return query.offset(offset).limit(limit).all()

    @staticmethod
    def get(db: Session, insight_id: str) -> AIInsight:
        insight = db.get(AIInsight, coerce_uuid(insight_id))
        if not insight:
            raise HTTPException(status_code=404, detail="Insight not found")
        return insight

    @staticmethod
    def acknowledge(db: Session, insight_id: str, person_id: str) -> AIInsight:
        insight = db.get(AIInsight, coerce_uuid(insight_id))
        if not insight:
            raise HTTPException(status_code=404, detail="Insight not found")
        insight.status = AIInsightStatus.acknowledged
        insight.is_acknowledged = True
        insight.acknowledged_at = datetime.now(UTC)
        insight.acknowledged_by_person_id = coerce_uuid(person_id)
        db.commit()
        db.refresh(insight)
        return insight

    @staticmethod
    def expire_stale(db: Session) -> int:
        """Mark expired insights. Called by scheduled task."""
        now = datetime.now(UTC)
        rows = (
            db.query(AIInsight)
            .filter(
                AIInsight.expires_at.isnot(None),
                AIInsight.expires_at <= now,
                AIInsight.status.in_([AIInsightStatus.completed, AIInsightStatus.pending]),
            )
            .all()
        )
        for row in rows:
            row.status = AIInsightStatus.expired
        db.commit()
        return len(rows)


insight_manager = InsightManager()
```

## 9. Settings Definitions

Add to `app/services/settings_spec.py` in the integration domain section:

```python
# Global AI kill switch (already exists in repo)
SettingSpec(
    domain=SettingDomain.integration,
    key="ai_enabled",
    env_var="AI_ENABLED",
    value_type=SettingValueType.boolean,
    default=False,
    label="Enable AI Features",
),

# LLM endpoints (already exist in repo): primary + optional secondary.
# Primary can point at a hosted provider (example: DeepSeek) and secondary at a self-hosted server (example: Llama).
# Primary:
# - vllm_base_url=https://api.deepseek.com
# - vllm_model=deepseek-chat
# - vllm_require_api_key=true
#
# Secondary:
# - vllm_secondary_base_url=https://llama.example.com
# - vllm_secondary_model=llama-3.1-8b-instruct
# - vllm_secondary_require_api_key=true|false

# Intelligence Engine settings
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_enabled",
    env_var="INTELLIGENCE_ENABLED",
    value_type=SettingValueType.boolean,
    default=False,
    label="Enable Intelligence Engine",
),
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_ticket_analyst_enabled",
    env_var="INTELLIGENCE_TICKET_ANALYST_ENABLED",
    value_type=SettingValueType.boolean,
    default=True,
    label="Enable Ticket Analyst Persona",
),
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_project_advisor_enabled",
    env_var="INTELLIGENCE_PROJECT_ADVISOR_ENABLED",
    value_type=SettingValueType.boolean,
    default=True,
    label="Enable Project Advisor Persona",
),
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_inbox_analyst_enabled",
    env_var="INTELLIGENCE_INBOX_ANALYST_ENABLED",
    value_type=SettingValueType.boolean,
    default=True,
    label="Enable Inbox Analyst Persona",
),
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_campaign_optimizer_enabled",
    env_var="INTELLIGENCE_CAMPAIGN_OPTIMIZER_ENABLED",
    value_type=SettingValueType.boolean,
    default=True,
    label="Enable Campaign Optimizer Persona",
),
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_dispatch_planner_enabled",
    env_var="INTELLIGENCE_DISPATCH_PLANNER_ENABLED",
    value_type=SettingValueType.boolean,
    default=True,
    label="Enable Dispatch Planner Persona",
),
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_vendor_analyst_enabled",
    env_var="INTELLIGENCE_VENDOR_ANALYST_ENABLED",
    value_type=SettingValueType.boolean,
    default=True,
    label="Enable Vendor Analyst Persona",
),
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_performance_coach_enabled",
    env_var="INTELLIGENCE_PERFORMANCE_COACH_ENABLED",
    value_type=SettingValueType.boolean,
    default=True,
    label="Enable Performance Coach Persona",
),
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_customer_success_enabled",
    env_var="INTELLIGENCE_CUSTOMER_SUCCESS_ENABLED",
    value_type=SettingValueType.boolean,
    default=True,
    label="Enable Customer Success Persona",
),
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_daily_token_budget",
    env_var="INTELLIGENCE_DAILY_TOKEN_BUDGET",
    value_type=SettingValueType.integer,
    default=500000,
    min_value=0,
    max_value=10000000,
    label="Daily Token Budget for Intelligence Engine",
),
SettingSpec(
    domain=SettingDomain.integration,
    key="intelligence_max_insights_per_run",
    env_var="INTELLIGENCE_MAX_INSIGHTS_PER_RUN",
    value_type=SettingValueType.integer,
    default=50,
    min_value=1,
    max_value=500,
    label="Max Insights per Scheduled Run",
),
```

Gating rule:
- `integration.ai_enabled` must be true (global)
- `integration.intelligence_enabled` must be true (engine scheduled/batch)

## 10. Celery Tasks -- `app/tasks/intelligence.py`

```python
from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.db import SessionLocal
from app.services.ai.engine import insight_manager, intelligence_engine
from app.services.ai.personas._registry import persona_registry

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.intelligence.run_scheduled_analysis")
def run_scheduled_analysis(persona_key: str | None = None) -> dict:
    """Run scheduled analysis for one or all personas.

    If persona_key is None, runs all personas that support scheduled execution.
    """
    session = SessionLocal()
    try:
        if persona_key:
            specs = [persona_registry.get(persona_key)]
        else:
            specs = [s for s in persona_registry.list_all() if s.supports_scheduled]

        results = {}
        for spec in specs:
            try:
                # Each persona's scheduled mode needs a batch scanner
                # that finds entities needing analysis
                count = _run_persona_batch(session, spec)
                results[spec.key] = {"generated": count}
            except Exception:
                logger.exception("Scheduled analysis failed for persona=%s", spec.key)
                results[spec.key] = {"error": True}

        return results
    finally:
        session.close()


def _run_persona_batch(session, spec) -> int:
    """Find entities in the persona's domain that need fresh analysis."""
    from app.services.ai.context_builders import batch_scanners

    scanner = batch_scanners.get(spec.domain)
    if not scanner:
        return 0

    entity_params_list = scanner(session, spec)
    count = 0
    for entity_type, entity_id, params in entity_params_list:
        try:
            intelligence_engine.invoke(
                session,
                persona_key=spec.key,
                params=params,
                entity_type=entity_type,
                entity_id=entity_id,
                trigger="scheduled",
            )
            count += 1
        except Exception:
            logger.exception(
                "Failed scheduled insight for persona=%s entity=%s/%s",
                spec.key, entity_type, entity_id,
            )
    return count


@celery_app.task(name="app.tasks.intelligence.expire_stale_insights")
def expire_stale_insights() -> dict:
    session = SessionLocal()
    try:
        expired = insight_manager.expire_stale(session)
        return {"expired": expired}
    except Exception:
        session.rollback()
        logger.exception("Failed to expire stale insights")
        raise
    finally:
        session.close()


@celery_app.task(name="app.tasks.intelligence.invoke_persona_async")
def invoke_persona_async(
    persona_key: str,
    params: dict,
    entity_type: str,
    entity_id: str | None = None,
    triggered_by_person_id: str | None = None,
) -> dict:
    """Async invocation of a persona -- for when the user doesn't want to wait."""
    session = SessionLocal()
    try:
        insight = intelligence_engine.invoke(
            session,
            persona_key=persona_key,
            params=params,
            entity_type=entity_type,
            entity_id=entity_id,
            trigger="on_demand",
            triggered_by_person_id=triggered_by_person_id,
        )
        return {"insight_id": str(insight.id), "status": insight.status.value}
    except Exception:
        session.rollback()
        logger.exception("Async persona invocation failed for %s", persona_key)
        raise
    finally:
        session.close()
```

**Scheduler registration** (add to `app/services/scheduler_config.py` in `build_beat_schedule()`):

```python
# Intelligence Engine scheduled analysis
ai_enabled = _effective_bool(
    session,
    SettingDomain.integration,
    "ai_enabled",
    "AI_ENABLED",
    False,
)
intelligence_enabled = _effective_bool(
    session,
    SettingDomain.integration,
    "intelligence_enabled",
    "INTELLIGENCE_ENABLED",
    False,
)
if ai_enabled and intelligence_enabled:
    _sync_scheduled_task(
        session,
        name="intelligence_scheduled_analysis",
        task_name="app.tasks.intelligence.run_scheduled_analysis",
        enabled=intelligence_enabled,
        interval_seconds=3600,  # hourly
    )
    _sync_scheduled_task(
        session,
        name="intelligence_expire_stale",
        task_name="app.tasks.intelligence.expire_stale_insights",
        enabled=True,
        interval_seconds=86400,  # daily
    )
```

## 11. API Endpoints

**Extend `app/api/ai.py`** to add insight endpoints:

```python
# New endpoints to add:

@router.get("/insights")
def list_insights(
    domain: str | None = None,
    persona_key: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
    auth=Depends(require_user_auth),
):
    # Access control:
    # - Default to restricting org-wide insight listing to authorized staff only
    #   (example: dependencies=[Depends(require_permission("reports:operations"))]).
    # - For entity-scoped insights, validate the user can access the underlying entity.
    items = insight_manager.list(
        db, domain=domain, persona_key=persona_key,
        entity_type=entity_type, entity_id=entity_id,
        status=status, severity=severity,
        limit=min(limit, 100), offset=offset,
    )
    return {"items": [InsightRead.from_orm(i) for i in items], "count": len(items)}


@router.get("/insights/{insight_id}")
def get_insight(insight_id: str, db=Depends(get_db), auth=Depends(require_user_auth)):
    return InsightRead.from_orm(insight_manager.get(db, insight_id))


@router.post("/insights/{insight_id}/acknowledge")
def acknowledge_insight(insight_id: str, db=Depends(get_db), auth=Depends(require_user_auth)):
    person_id = str(auth.get("person_id"))
    insight = insight_manager.acknowledge(db, insight_id, person_id)
    return {"id": str(insight.id), "status": insight.status.value}


@router.post("/analyze/{persona_key}")
def invoke_analysis(
    persona_key: str,
    payload: AnalyzeRequest,    # Pydantic model with entity_type, entity_id, params
    db=Depends(get_db),
    auth=Depends(require_user_auth),
):
    """On-demand invocation of an AI persona."""
    person_id = str(auth.get("person_id")) if auth else None
    insight = intelligence_engine.invoke(
        db,
        persona_key=persona_key,
        params=payload.params or {},
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        trigger="on_demand",
        triggered_by_person_id=person_id,
        request=None,
    )
    return InsightRead.from_orm(insight)


@router.post("/analyze/{persona_key}/async")
def invoke_analysis_async(
    persona_key: str,
    payload: AnalyzeRequest,
    db=Depends(get_db),
    auth=Depends(require_user_auth),
):
    """Queue an async persona invocation, returns immediately."""
    person_id = str(auth.get("person_id")) if auth else None
    from app.tasks.intelligence import invoke_persona_async
    task = invoke_persona_async.delay(
        persona_key,
        payload.params or {},
        payload.entity_type,
        payload.entity_id,
        person_id,
    )
    return {"task_id": task.id, "persona_key": persona_key}


@router.get("/personas")
def list_personas(db=Depends(get_db), auth=Depends(require_user_auth)):
    """List all registered personas and their capabilities."""
    specs = persona_registry.list_all()
    return {
        "personas": [
            {
                "key": s.key,
                "name": s.name,
                "domain": s.domain,
                "description": s.description,
                "supports_scheduled": s.supports_scheduled,
            }
            for s in specs
        ]
    }
```

## 12. Migration Plan for Existing Use Cases

The three existing AI features (`crm_reply`, `ticket_summary`, `performance_review`) should be migrated incrementally without breaking existing callers.

**Phase 1 (immediate):** Build the engine and first personas. The existing use case functions keep working as-is.

**Phase 2 (after engine is stable):** Refactor existing use cases to delegate to the engine internally while preserving their external signatures:

```python
# app/services/ai/use_cases/ticket_summary.py -- updated
def summarize_ticket(db, *, request, ticket_id, actor_person_id, ...):
    # Delegate to engine
    insight = intelligence_engine.invoke(
        db,
        persona_key="ticket_analyst",
        params={"ticket_id": ticket_id, "mode": "summary"},
        entity_type="ticket",
        entity_id=ticket_id,
        trigger="on_demand",
        triggered_by_person_id=actor_person_id,
        request=request,
    )
    # Convert to legacy dataclass format for backward compatibility
    return TicketAISummary(
        summary=insight.summary,
        next_actions=insight.recommendations or [],
        meta={"provider": insight.llm_provider, "model": insight.llm_model},
    )
```

**Phase 3 (cleanup):** Update API callers to use the new `/api/v1/ai/analyze/ticket_analyst` endpoint directly. Deprecate old endpoints.

The `performance_review` path is more nuanced because it writes to `AgentPerformanceReview` specifically. The `performance_coach` persona will also write to `AIInsight`, but the existing review table stays. The persona generates the review content; a thin adapter writes it to both tables.

## 13. Schemas -- `app/schemas/ai_insight.py`

```python
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AnalyzeRequest(BaseModel):
    entity_type: str = Field(min_length=1, max_length=80)
    entity_id: str | None = None
    params: dict[str, Any] | None = None


class InsightRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    persona_key: str
    domain: str
    severity: str
    status: str
    entity_type: str
    entity_id: str | None
    title: str
    summary: str
    structured_output: dict | None = None
    confidence_score: float | None = None
    recommendations: list | None = None
    llm_provider: str
    llm_model: str
    llm_tokens_in: int | None = None
    llm_tokens_out: int | None = None
    generation_time_ms: int | None = None
    trigger: str
    triggered_by_person_id: str | None = None
    is_acknowledged: bool
    acknowledged_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
```

## 14. Database Migration

The migration needs to:
1. Create the `InsightDomain`, `InsightSeverity`, `AIInsightStatus` enum types with `checkfirst=True`.
2. Create the `ai_insights` table.
3. Create the five indexes.

```python
"""Add AI insights table for Intelligence Engine.

Revision ID: xxxx
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade():
    insight_domain_enum = postgresql.ENUM(
        'tickets', 'projects', 'inbox', 'campaigns', 'dispatch',
        'vendors', 'performance', 'customer_success',
        name='insightdomain', create_type=False,
    )
    insight_severity_enum = postgresql.ENUM(
        'info', 'suggestion', 'warning', 'critical',
        name='insightseverity', create_type=False,
    )
    insight_status_enum = postgresql.ENUM(
        'pending', 'completed', 'failed', 'acknowledged', 'actioned', 'expired',
        name='aiinsightstatus', create_type=False,
    )

    bind = op.get_bind()
    insight_domain_enum.create(bind, checkfirst=True)
    insight_severity_enum.create(bind, checkfirst=True)
    insight_status_enum.create(bind, checkfirst=True)

    op.create_table(
        'ai_insights',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('persona_key', sa.String(80), nullable=False),
        sa.Column('domain', insight_domain_enum, nullable=False),
        sa.Column('severity', insight_severity_enum, server_default='info', nullable=False),
        sa.Column('status', insight_status_enum, server_default='pending', nullable=False),
        sa.Column('entity_type', sa.String(80), nullable=False),
        sa.Column('entity_id', sa.String(120)),
        sa.Column('title', sa.String(300), nullable=False),
        sa.Column('summary', sa.Text, nullable=False),
        sa.Column('structured_output', sa.JSON),
        sa.Column('confidence_score', sa.Numeric(3, 2)),
        sa.Column('recommendations', sa.JSON),
        sa.Column('llm_provider', sa.String(40), nullable=False, server_default='vllm'),
        sa.Column('llm_model', sa.String(100), nullable=False),
        sa.Column('llm_tokens_in', sa.Integer),
        sa.Column('llm_tokens_out', sa.Integer),
        sa.Column('llm_endpoint', sa.String(20)),
        sa.Column('generation_time_ms', sa.Integer),
        sa.Column('trigger', sa.String(40), nullable=False),
        sa.Column('triggered_by_person_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('people.id')),
        sa.Column('is_acknowledged', sa.Boolean, server_default='false'),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True)),
        sa.Column('acknowledged_by_person_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('people.id')),
        sa.Column('expires_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_ai_insight_domain_status', 'ai_insights', ['domain', 'status'])
    op.create_index('ix_ai_insight_entity', 'ai_insights', ['entity_type', 'entity_id'])
    op.create_index('ix_ai_insight_persona', 'ai_insights', ['persona_key'])
    op.create_index('ix_ai_insight_created', 'ai_insights', ['created_at'])
    op.create_index('ix_ai_insight_severity', 'ai_insights', ['severity'])


def downgrade():
    op.drop_table('ai_insights')
    bind = op.get_bind()
    sa.Enum(name='aiinsightstatus').drop(bind, checkfirst=True)
    sa.Enum(name='insightseverity').drop(bind, checkfirst=True)
    sa.Enum(name='insightdomain').drop(bind, checkfirst=True)
```

## 15. Registration Touchpoints (Existing Files to Modify)

| File | Change |
|------|--------|
| `app/models/__init__.py` | Add imports for `AIInsight`, `AIInsightStatus`, `InsightDomain`, `InsightSeverity` |
| `app/services/ai/__init__.py` | Re-export `intelligence_engine`, `persona_registry`, `insight_manager` |
| `app/tasks/__init__.py` | Import tasks from `app.tasks.intelligence` |
| `app/services/scheduler_config.py` | Add scheduled task registrations for the engine |
| `app/services/settings_spec.py` | Add ~12 new `SettingSpec` entries in the integration domain |
| `app/main.py` | Already imports `ai_router` which will include new endpoints |
| `app/web/admin/__init__.py` | Include `intelligence_router` for admin web UI |
| `templates/components/navigation/admin_sidebar.html` | Add "Intelligence" nav item |

## 16. Implementation Order

### Phase 1: Foundation (Days 1-3)
1. **Model** -- `app/models/ai_insight.py` + migration + register in `__init__.py`
2. **Base types** -- `app/services/ai/personas/_base.py`, `_registry.py`, `__init__.py`
3. **Output parser** -- `app/services/ai/output_parsers.py`
4. **Engine** -- `app/services/ai/engine.py` with `IntelligenceEngine` and `InsightManager`
5. **Schema** -- `app/schemas/ai_insight.py`
6. **Settings** -- Add intelligence settings to `settings_spec.py`
7. **Tests** -- `tests/test_intelligence_engine.py` -- test registry, parsing, engine pipeline with mocked AI gateway

### Phase 2: First Two Personas (Days 4-5)
8. **Context builder: tickets** -- `app/services/ai/context_builders/tickets.py`
9. **Ticket Analyst persona** -- `app/services/ai/personas/ticket_analyst.py`
10. **Context builder: inbox** -- `app/services/ai/context_builders/inbox.py`
11. **Inbox Analyst persona** -- `app/services/ai/personas/inbox_analyst.py`
12. **API endpoints** -- Extend `app/api/ai.py`
13. **Tests** -- `tests/test_ai_personas.py` -- test each persona with mocked context

### Phase 3: Remaining Personas (Days 6-8)
14. Context builders and personas for: `project_advisor`, `campaign_optimizer`, `dispatch_planner`, `vendor_analyst`, `performance_coach`, `customer_success`
15. Each persona gets its own context builder and test file

### Phase 4: Scheduled Execution (Days 9-10)
16. **Batch scanners** -- `app/services/ai/context_builders/batch_scanners.py` -- functions that find entities needing analysis per domain
17. **Tasks** -- `app/tasks/intelligence.py`
18. **Scheduler** -- Register in `scheduler_config.py`
19. **Task registration** -- `app/tasks/__init__.py`

### Phase 5: Admin UI (Days 11-12)
20. **Web routes** -- `app/web/admin/intelligence.py`
21. **Templates** -- `templates/admin/intelligence/index.html`, `detail.html`, `_insights_table.html`
22. **Sidebar** -- Add Intelligence link to sidebar

### Phase 6: Migration & Polish (Days 13-14)
23. Refactor existing `ticket_summary` to delegate to engine
24. Refactor existing `crm_reply` to delegate to engine
25. Integration tests
26. Documentation

## 17. Key Design Decisions and Trade-offs

**Why a flat registry instead of class inheritance?**
Personas are fundamentally data (prompts, schemas, settings) not behavior. Using frozen dataclasses makes them easy to test, serialize, and reason about. The context builders are plain functions, not methods on a persona class. This avoids the "god class" anti-pattern.

**Why a single `ai_insights` table instead of per-persona tables?**
All insights share the same lifecycle (created, acknowledged, expired). The `structured_output` JSON column holds persona-specific data. This simplifies querying ("show me all recent insights across all domains") and the admin dashboard. If volume becomes a concern, we can partition by domain later.

**Why not convert existing use cases immediately?**
The existing `crm_reply` and `ticket_summary` endpoints have callers. We keep them working as-is in Phase 1-4, then migrate them in Phase 6 once the engine is proven stable. This avoids breaking changes during development.

**Token budget control:**
The `intelligence_daily_token_budget` setting and per-persona `max_context_chars`/`default_max_tokens` constraints provide cost control. The engine should track daily token usage (via a Redis counter or a sum query on `ai_insights.llm_tokens_in + llm_tokens_out` for today) and refuse new invocations when budget is exceeded.

**Why `generate_with_fallback` instead of `generate`?**
Using the fallback mechanism means the engine is resilient to primary endpoint failures. The `llm_endpoint` column on `AIInsight` records which endpoint actually served the request, enabling cost tracking per provider.

---

### Critical Reference Files
- `app/services/ai/gateway.py` - Core AI gateway that the engine calls; must understand its interface (generate, generate_with_fallback, enabled)
- `app/services/ai/use_cases/ticket_summary.py` - Existing use case pattern to follow and later migrate; demonstrates context gathering + LLM call + structured parsing
- `app/models/performance.py` - Reference model for how AI-generated analysis results are stored (AgentPerformanceReview with LLM metadata columns); closest analog to the AIInsight model
- `app/services/settings_spec.py` - Must be modified to add ~12 intelligence engine SettingSpec entries; defines the feature flag pattern
- `app/services/scheduler_config.py` - Must be modified to register intelligence scheduled tasks; defines the _sync_scheduled_task pattern used throughout
