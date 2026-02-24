---
name: add-ai-persona
description: Create a new AI intelligence persona with context builder, output schema, and quality scorer
arguments:
  - name: persona_info
    description: "Persona purpose (e.g. 'fiber health analyzer for OLT/closure monitoring')"
---

# Add AI Persona

Create a new AI intelligence persona for the DotMac Omni CRM intelligence engine.

## Steps

### 1. Understand the request
Parse `$ARGUMENTS` to determine:
- **Domain**: tickets, crm, projects, workforce, network, fiber, customers, vendors
- **Entity type**: what the persona analyzes (ticket, conversation, project, work_order, etc.)
- **Purpose**: triage, coaching, risk assessment, optimization, health monitoring
- **Trigger mode**: `on_demand` (user-triggered) or `scheduled` (batch scanner)

### 2. Study the existing patterns
Read these reference files:

- **Persona base**: `app/services/ai/personas/_base.py` — `PersonaSpec`, `OutputSchema`, `OutputField`, `ContextQualityResult`
- **Persona registry**: `app/services/ai/personas/_registry.py` — `persona_registry.register()`
- **Existing personas**: `app/services/ai/personas/` — `ticket_analyst.py`, `inbox_analyst.py`, `project_advisor.py`, `performance_coach.py`, `vendor_analyst.py`, `campaign_optimizer.py`, `dispatch_planner.py`, `customer_success.py`
- **Context builders**: `app/services/ai/context_builders/` — `tickets.py`, `conversations.py`, `projects.py`, `vendors.py`, `performance.py`
- **Batch scanners**: `app/services/ai/context_builders/batch_scanners.py` — candidate selection for scheduled runs
- **Intelligence engine**: `app/services/ai/engine.py` — orchestrator (invoke, gate, persist)
- **Output parser**: `app/services/ai/output_parsers.py` — `parse_json_object()`, `require_keys()`
- **Redaction**: `app/services/ai/redaction.py` — `redact_text()` for PII safety
- **Insight model**: `app/models/ai_insight.py` — `InsightDomain` enum, `AIInsight` model

### 3. Create the context builder
Create `app/services/ai/context_builders/{domain}.py`:

```python
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.person import Person
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def gather_{domain}_context(db: Session, params: dict[str, Any]) -> str:
    """Context builder for {persona_name}.

    Required params:
      - {entity}_id: UUID of the entity to analyze
    """
    entity_id = params.get("{entity}_id")
    if not entity_id:
        raise ValueError("{entity}_id is required")

    entity = db.get(Model, coerce_uuid(entity_id))
    if not entity:
        raise ValueError("{Entity} not found")

    max_items = min(int(params.get("max_items", 10)), 30)
    max_chars = int(params.get("max_chars", 600))

    def _person_name(person_id) -> str | None:
        if not person_id:
            return None
        p = db.get(Person, person_id)
        return redact_text(p.display_name or "", max_chars=120) if p else None

    lines: list[str] = [
        f"{Entity} ID: {str(entity.id)[:8]}",
        f"Status: {entity.status.value if hasattr(entity.status, 'value') else str(entity.status)}",
        f"Created: {entity.created_at.isoformat() if entity.created_at else 'unknown'}",
        f"Updated: {entity.updated_at.isoformat() if entity.updated_at else 'unknown'}",
        # Add domain-specific fields here, always using redact_text()
    ]

    # Batch load related entities (N+1 prevention)
    # related = db.query(Related).filter(Related.entity_id == entity.id).limit(max_items).all()

    return "\n".join([line for line in lines if line.strip()])
```

**Context builder rules:**
- Always use `redact_text()` for any user-supplied content (names, descriptions, comments)
- Batch load related entities (never query inside loops)
- Respect `max_items` and `max_chars` params for token budget control
- Return a single string with newline-separated lines
- Include status counts and summary metrics when possible

### 4. Create the quality scorer (optional)
If the persona needs a data quality gate:

```python
def score_{domain}_quality(db: Session, params: dict[str, Any]) -> ContextQualityResult:
    """Check if enough data exists for meaningful analysis."""
    from app.services.ai.personas._base import ContextQualityResult

    entity_id = params.get("{entity}_id")
    if not entity_id:
        return ContextQualityResult(score=0.0, missing_fields=["{entity}_id"])

    entity = db.get(Model, coerce_uuid(entity_id))
    if not entity:
        return ContextQualityResult(score=0.0, missing_fields=["{entity}"])

    field_scores = {}
    missing = []

    # Score each field (0.0 = missing, 1.0 = populated)
    if entity.description:
        field_scores["description"] = 1.0
    else:
        field_scores["description"] = 0.0
        missing.append("description")

    # Score related data availability
    related_count = db.query(Related).filter(Related.entity_id == entity.id).count()
    field_scores["related_items"] = min(1.0, related_count / 3)
    if related_count == 0:
        missing.append("related_items")

    total = sum(field_scores.values()) / len(field_scores) if field_scores else 0.0
    return ContextQualityResult(score=total, field_scores=field_scores, missing_fields=missing)
```

### 5. Create the persona
Create `app/services/ai/personas/{persona_key}.py`:

```python
from __future__ import annotations

from app.models.ai_insight import InsightDomain
from app.services.ai.context_builders.{domain} import gather_{domain}_context
from app.services.ai.personas._base import OutputField, OutputSchema, PersonaSpec
from app.services.ai.personas._registry import persona_registry

_OUTPUT_SCHEMA = OutputSchema(
    fields=(
        OutputField(name="summary", type="string", description="Concise analysis summary (2-3 sentences)"),
        OutputField(name="risk_level", type="string", description="Risk level: low, medium, high, critical"),
        OutputField(name="findings", type="array[string]", description="Key findings (3-5 bullet points)"),
        OutputField(name="recommendations", type="array[string]", description="Actionable recommendations"),
        OutputField(name="confidence", type="number", description="Confidence score 0.0-1.0"),
    )
)

_SYSTEM_PROMPT = """\
You are a {domain} analyst for a telecommunications service provider.
Analyze the provided data and return a JSON object with these keys:

{output_schema_instruction}

Be specific and actionable in your recommendations.
Reference specific data points from the context.
Return valid JSON only — no markdown, no extra text.
"""


def _severity_classifier(output: dict) -> str:
    """Map output risk_level to insight severity."""
    risk = str(output.get("risk_level", "")).lower()
    if risk in ("critical", "high"):
        return "warning"
    if risk == "medium":
        return "suggestion"
    return "info"


_SPEC = PersonaSpec(
    key="{persona_key}",
    name="{Persona Display Name}",
    domain=InsightDomain.{insight_domain},  # tickets, crm, projects, network, etc.
    description="{One-line description of what this persona does}",
    system_prompt=_SYSTEM_PROMPT.format(output_schema_instruction=_OUTPUT_SCHEMA.to_instruction()),
    output_schema=_OUTPUT_SCHEMA,
    context_builder=gather_{domain}_context,
    default_max_tokens=1200,
    default_endpoint="primary",
    supports_scheduled=False,  # Set True for batch scanners
    insight_ttl_hours=72,
    severity_classifier=_severity_classifier,
    # Uncomment for quality gating:
    # context_quality_scorer=score_{domain}_quality,
    # min_context_quality=0.3,
    # skip_on_low_quality=True,
)

persona_registry.register(_SPEC)
```

### 6. Register the persona
Add import to `app/services/ai/personas/__init__.py`:

```python
from app.services.ai.personas import {persona_key}  # noqa: F401
```

### 7. Add a use case (optional, for on-demand invocation)
Create `app/services/ai/use_cases/{use_case}.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.ai.engine import intelligence_engine
from app.services.audit_helpers import log_audit_event
from app.services.common import coerce_uuid


@dataclass(frozen=True)
class {Entity}Analysis:
    summary: str
    findings: list[str]
    recommendations: list[str]
    meta: dict


def analyze_{entity}(
    db: Session,
    *,
    request,
    {entity}_id: str,
    actor_person_id: str | None,
) -> {Entity}Analysis:
    insight = intelligence_engine.invoke(
        db,
        persona_key="{persona_key}",
        params={{"{entity}_id": {entity}_id}},
        entity_type="{entity}",
        entity_id={entity}_id,
        trigger="on_demand",
        triggered_by_person_id=actor_person_id,
    )
    output = insight.structured_output or {}

    log_audit_event(
        db, request,
        action="ai_{persona_key}",
        entity_type="{entity}",
        entity_id={entity}_id,
        actor_id=actor_person_id,
        metadata={{"insight_id": str(insight.id)}},
        status_code=200, is_success=True,
    )

    return {Entity}Analysis(
        summary=str(output.get("summary") or "No analysis generated."),
        findings=output.get("findings") or [],
        recommendations=output.get("recommendations") or [],
        meta={{"provider": insight.llm_provider, "model": insight.llm_model, "insight_id": str(insight.id)}},
    )
```

### 8. Add batch scanner (if supports_scheduled=True)
Add a candidate scanner to `app/services/ai/context_builders/batch_scanners.py`:

```python
def scan_{domain}_candidates(db: Session, *, limit: int = 20) -> list[dict[str, Any]]:
    """Find {entities} that need analysis."""
    cutoff = datetime.now(UTC) - timedelta(hours=72)
    candidates = (
        db.query(Model)
        .filter(Model.is_active.is_(True))
        .filter(Model.status.in_([Status.active, Status.open]))
        # Exclude recently analyzed
        .filter(~Model.id.in_(
            db.query(AIInsight.entity_id)
            .filter(AIInsight.persona_key == "{persona_key}")
            .filter(AIInsight.created_at > cutoff)
        ))
        .order_by(Model.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [{"{entity}_id": str(c.id)} for c in candidates]
```

### 9. Verify
```bash
# Syntax check
python3 -c "import ast; ast.parse(open('app/services/ai/personas/{persona_key}.py').read())"
python3 -c "import ast; ast.parse(open('app/services/ai/context_builders/{domain}.py').read())"

# Import check
python3 -c "from app.services.ai.personas.{persona_key} import _SPEC; print(_SPEC.key)"

# Lint
ruff check app/services/ai/personas/{persona_key}.py app/services/ai/context_builders/{domain}.py --fix
ruff format app/services/ai/personas/{persona_key}.py app/services/ai/context_builders/{domain}.py
```

### 10. Checklist
- [ ] Context builder uses `redact_text()` on all user content
- [ ] Context builder respects `max_items` and `max_chars` params
- [ ] No N+1 queries in context builder (batch load with `.in_()`)
- [ ] Output schema has `required=True` on essential fields
- [ ] System prompt instructs "return JSON only"
- [ ] Persona registered in `_registry` via `__init__.py` import
- [ ] Severity classifier maps output to `info`/`suggestion`/`warning`/`critical`
- [ ] InsightDomain enum value exists in `app/models/ai_insight.py`
- [ ] Use case logs audit event with `insight_id` in metadata
