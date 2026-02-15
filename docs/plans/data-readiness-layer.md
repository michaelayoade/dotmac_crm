# Data Readiness Layer — Intelligence Engine Addition

## Purpose

The Intelligence Engine calls LLMs with domain context gathered from the database. But it has no way to know whether that context is *rich enough* to produce a useful insight. A ticket with no description, no comments, no customer link, and no SLA events will produce a low-quality analysis that wastes tokens and erodes user trust.

This layer adds **context quality scoring**, **minimum quality gates**, **domain data health reporting**, and **insight quality tracking** — all as lightweight additions to the existing engine, not a separate module.

## Design Principles

1. **Non-breaking** — Context builders keep their existing `(db, params) -> str` signature. Quality scoring is a parallel concern, not a signature change.
2. **Opt-in per persona** — Each persona declares its own quality thresholds. Personas that work with sparse data (e.g., new ticket triage) can set low thresholds.
3. **Observable** — Quality scores are persisted on every insight so you can track trends without additional infrastructure.
4. **Cheap** — Quality scoring is pure Python math against data already loaded by the context builder. No extra DB queries, no LLM calls.

## Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `app/services/ai/personas/_base.py` | MODIFY | Add `ContextQuality` dataclass, quality fields to `PersonaSpec` |
| `app/services/ai/engine.py` | MODIFY | Call quality scorer before LLM, persist score, gate on threshold |
| `app/services/ai/context_builders/tickets.py` | MODIFY | Add `score_ticket_context_quality()` |
| `app/services/ai/context_builders/inbox.py` | MODIFY | Add `score_inbox_context_quality()` |
| `app/services/ai/context_builders/projects.py` | MODIFY | Add `score_project_context_quality()` |
| `app/services/ai/context_builders/campaigns.py` | MODIFY | Add `score_campaign_context_quality()` |
| `app/services/ai/context_builders/dispatch.py` | MODIFY | Add `score_dispatch_context_quality()` |
| `app/services/ai/context_builders/vendors.py` | MODIFY | Add `score_vendor_context_quality()` |
| `app/services/ai/context_builders/performance.py` | MODIFY | Add `score_performance_context_quality()` |
| `app/services/ai/context_builders/customers.py` | MODIFY | Add `score_customer_context_quality()` |
| `app/services/ai/data_health.py` | NEW | Domain data health aggregation service |
| `app/models/ai_insight.py` | MODIFY | Add `context_quality_score` column |
| `app/api/ai.py` | MODIFY | Add `/data-health` endpoint |
| `alembic/versions/xxxx_add_context_quality_score.py` | NEW | Migration for new column |
| `tests/test_data_readiness.py` | NEW | Tests for quality scoring + gating |

---

## 1. Base Types — `_base.py` Additions

Add a `ContextQuality` result dataclass and quality-related fields to `PersonaSpec`:

```python
# Add to app/services/ai/personas/_base.py

@dataclass(frozen=True)
class ContextQualityResult:
    """Result of evaluating context data completeness for a persona."""
    score: float                          # 0.0 (empty) to 1.0 (fully populated)
    field_scores: dict[str, float]        # individual field/signal scores
    missing_fields: list[str]             # fields that scored 0
    sufficient: bool                      # score >= threshold


def _default_quality_scorer(db: Session, params: dict[str, Any]) -> ContextQualityResult:
    """Fallback scorer that always returns sufficient. Used when no scorer is defined."""
    return ContextQualityResult(score=1.0, field_scores={}, missing_fields=[], sufficient=True)
```

Add to `PersonaSpec`:

```python
@dataclass(frozen=True)
class PersonaSpec:
    # ... existing fields ...

    # Data readiness
    context_quality_scorer: Callable[[Session, dict[str, Any]], ContextQualityResult] = _default_quality_scorer
    min_context_quality: float = 0.0      # 0.0 = no gate (always proceed)
    skip_on_low_quality: bool = True      # if True, skip LLM call; if False, proceed but tag insight
```

The `context_quality_scorer` receives the same `(db, params)` as the context builder. It runs *before* the context builder to avoid building a full prompt string only to throw it away. It queries the same entity but only checks for field presence — no heavy text assembly.

---

## 2. Engine Changes — `engine.py`

Modify `invoke()` to check quality before calling the LLM:

```python
def invoke(self, db, *, persona_key, params, entity_type, entity_id, trigger, triggered_by_person_id=None):
    # ... existing enabled/budget/persona checks ...

    spec = persona_registry.get(persona_key)
    if not self._persona_enabled(db, spec.setting_key):
        raise AIClientError(f"Persona disabled: {persona_key}")

    # ── NEW: Data readiness check ──────────────────────────────
    quality = spec.context_quality_scorer(db, params or {})

    if not quality.sufficient and spec.skip_on_low_quality:
        # Create a skipped insight record for observability
        insight = AIInsight(
            persona_key=spec.key,
            domain=spec.domain,
            severity=InsightSeverity.info,
            status=AIInsightStatus.skipped,
            entity_type=entity_type,
            entity_id=entity_id,
            title=f"{spec.name}: insufficient data",
            summary=f"Skipped — context quality {quality.score:.0%} below threshold {spec.min_context_quality:.0%}. "
                    f"Missing: {', '.join(quality.missing_fields[:5])}.",
            structured_output={"quality": quality.field_scores, "missing": quality.missing_fields},
            context_quality_score=quality.score,
            llm_provider="n/a",
            llm_model="n/a",
            llm_tokens_in=0,
            llm_tokens_out=0,
            generation_time_ms=0,
            trigger=trigger,
            triggered_by_person_id=coerce_uuid(triggered_by_person_id) if triggered_by_person_id else None,
        )
        db.add(insight)
        db.commit()
        db.refresh(insight)
        return insight
    # ── END data readiness check ───────────────────────────────

    started = time.monotonic()
    context = spec.context_builder(db, params or {})
    # ... rest of existing invoke logic ...

    # Persist quality score on the insight (whether gated or not)
    insight = AIInsight(
        # ... existing fields ...
        context_quality_score=quality.score,     # NEW
    )
    # ... rest of existing persist/audit logic ...
```

Key behaviors:
- **`skip_on_low_quality=True` (default)**: Low-quality entities get a `skipped` insight record (zero tokens) so batch scanners don't re-attempt them until data improves.
- **`skip_on_low_quality=False`**: The LLM is called anyway, but the low quality score is persisted for filtering/reporting.
- **`min_context_quality=0.0` (default)**: Existing personas work unchanged — no gate, quality scored but never blocks.

---

## 3. AIInsight Model Changes

Add one column and one enum value:

```python
# app/models/ai_insight.py

class AIInsightStatus(enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"           # NEW — insufficient data quality
    acknowledged = "acknowledged"
    actioned = "actioned"
    expired = "expired"

class AIInsight(Base):
    # ... existing columns ...

    # Data readiness
    context_quality_score: Mapped[float | None] = mapped_column(Numeric(3, 2))  # 0.00–1.00
```

Migration:

```python
def upgrade():
    # Add context_quality_score column
    op.add_column("ai_insights", sa.Column("context_quality_score", sa.Numeric(3, 2)))
    op.create_index("ix_ai_insights_context_quality", "ai_insights", ["context_quality_score"])

    # Add 'skipped' to aiinsightstatus enum
    op.execute("ALTER TYPE aiinsightstatus ADD VALUE IF NOT EXISTS 'skipped' AFTER 'failed'")

def downgrade():
    op.drop_index("ix_ai_insights_context_quality", table_name="ai_insights")
    op.drop_column("ai_insights", "context_quality_score")
    # Note: PostgreSQL cannot remove enum values; 'skipped' remains but is unused
```

---

## 4. Context Quality Scorers

Each scorer checks field presence/completeness for the data the persona actually uses. The pattern is the same across all domains: check the entity exists, check each signal the context builder would use, return weighted scores.

### 4a. Tickets (`context_builders/tickets.py`)

```python
from app.services.ai.personas._base import ContextQualityResult


def score_ticket_context_quality(db: Session, params: dict[str, Any]) -> ContextQualityResult:
    """Score data completeness for ticket analysis."""
    ticket_id = params.get("ticket_id")
    if not ticket_id:
        return ContextQualityResult(score=0.0, field_scores={}, missing_fields=["ticket_id"], sufficient=False)

    ticket = db.get(Ticket, coerce_uuid(ticket_id))
    if not ticket:
        return ContextQualityResult(score=0.0, field_scores={}, missing_fields=["ticket"], sufficient=False)

    scores: dict[str, float] = {}
    missing: list[str] = []

    # Core fields (higher weight — these drive the analysis)
    scores["title"] = 1.0 if ticket.title and len(ticket.title.strip()) > 5 else 0.0
    scores["description"] = 1.0 if ticket.description and len(ticket.description.strip()) > 20 else 0.0
    scores["status"] = 1.0 if ticket.status else 0.0
    scores["priority"] = 1.0 if ticket.priority else 0.0

    # Relationship fields (medium weight — enrich the analysis)
    scores["customer"] = 1.0 if ticket.customer_person_id else 0.0
    scores["assignee"] = 1.0 if ticket.assigned_to_person_id else 0.0

    # Activity signals (lower weight — but critical for quality insights)
    comment_count = (
        db.query(func.count(TicketComment.id))
        .filter(TicketComment.ticket_id == ticket.id)
        .scalar() or 0
    )
    scores["comments"] = min(1.0, comment_count / 2)  # 2+ comments = full score

    sla_count = (
        db.query(func.count(TicketSlaEvent.id))
        .filter(TicketSlaEvent.ticket_id == ticket.id)
        .scalar() or 0
    )
    scores["sla_events"] = 1.0 if sla_count > 0 else 0.0

    missing = [k for k, v in scores.items() if v == 0.0]

    # Weighted composite: core fields matter more
    weights = {
        "title": 0.15, "description": 0.25, "status": 0.05, "priority": 0.05,
        "customer": 0.10, "assignee": 0.10, "comments": 0.20, "sla_events": 0.10,
    }
    total = sum(scores.get(k, 0) * w for k, w in weights.items())

    # Threshold comes from the PersonaSpec, but we report sufficiency here too
    # using a sensible standalone default (0.3 = at minimum need title + description)
    threshold = params.get("_quality_threshold", 0.3)

    return ContextQualityResult(
        score=round(total, 2),
        field_scores=scores,
        missing_fields=missing,
        sufficient=total >= threshold,
    )
```

### 4b. Inbox (`context_builders/inbox.py`)

```python
def score_inbox_context_quality(db: Session, params: dict[str, Any]) -> ContextQualityResult:
    conversation_id = params.get("conversation_id")
    if not conversation_id:
        return ContextQualityResult(score=0.0, field_scores={}, missing_fields=["conversation_id"], sufficient=False)

    conv = db.get(CrmConversation, coerce_uuid(conversation_id))
    if not conv:
        return ContextQualityResult(score=0.0, field_scores={}, missing_fields=["conversation"], sufficient=False)

    scores: dict[str, float] = {}

    scores["channel"] = 1.0 if conv.channel else 0.0
    scores["status"] = 1.0 if conv.status else 0.0
    scores["contact"] = 1.0 if conv.contact_id else 0.0
    scores["agent"] = 1.0 if conv.assigned_agent_id else 0.0

    msg_count = (
        db.query(func.count(CrmMessage.id))
        .filter(CrmMessage.conversation_id == conv.id)
        .scalar() or 0
    )
    scores["messages"] = min(1.0, msg_count / 3)  # 3+ messages = full score
    scores["has_inbound"] = 1.0 if msg_count > 0 else 0.0  # at least one message

    missing = [k for k, v in scores.items() if v == 0.0]

    weights = {
        "channel": 0.05, "status": 0.05, "contact": 0.15, "agent": 0.10,
        "messages": 0.40, "has_inbound": 0.25,
    }
    total = sum(scores.get(k, 0) * w for k, w in weights.items())
    threshold = params.get("_quality_threshold", 0.3)

    return ContextQualityResult(
        score=round(total, 2),
        field_scores=scores,
        missing_fields=missing,
        sufficient=total >= threshold,
    )
```

### 4c–4h. Remaining Domains

Each follows the identical pattern. The fields checked match what the corresponding `gather_*_context()` function actually uses:

| Domain | Key Signals Checked | Weights Focus |
|--------|-------------------|---------------|
| **Projects** | title, description, status, tasks (count), assigned members, due dates | tasks (0.30), description (0.20) |
| **Campaigns** | name, channel, status, audience filter, message template, recipient count | recipients (0.25), template (0.25) |
| **Dispatch** | work order exists, technician assigned, scheduled time, location, equipment | technician (0.20), schedule (0.20) |
| **Vendors** | vendor exists, active quotes/WOs, rating history, contact info | activity (0.30), contact (0.15) |
| **Performance** | person exists, has scores in period, has activity across domains | domain coverage (0.40), score count (0.30) |
| **Customers** | contact exists, has conversations, has tickets, has subscription/service | activity breadth (0.35), contact completeness (0.25) |

Implementation is mechanical — each is 30-50 lines following the ticket scorer pattern.

---

## 5. Persona Registration Updates

Update each persona's `PersonaSpec` to wire in its quality scorer and set appropriate thresholds:

```python
# app/services/ai/personas/ticket_analyst.py

from app.services.ai.context_builders.tickets import score_ticket_context_quality

_spec = PersonaSpec(
    # ... existing fields ...
    context_quality_scorer=score_ticket_context_quality,
    min_context_quality=0.30,       # need at least title + description + some activity
    skip_on_low_quality=True,
)
```

Recommended thresholds by persona:

| Persona | `min_context_quality` | `skip_on_low_quality` | Rationale |
|---------|----------------------|----------------------|-----------|
| `ticket_analyst` | 0.30 | True | Needs title + description minimum |
| `inbox_analyst` | 0.35 | True | Needs messages to analyze |
| `project_advisor` | 0.25 | True | Can advise on sparse projects |
| `campaign_optimizer` | 0.40 | True | Needs audience + template + some delivery data |
| `dispatch_planner` | 0.30 | True | Needs WO + technician + schedule |
| `vendor_analyst` | 0.25 | False | Can flag *lack* of vendor data as a finding |
| `performance_coach` | 0.35 | True | Needs scores across multiple domains |
| `customer_success` | 0.20 | False | Can flag thin customer records as a risk signal |

---

## 6. Data Health Service — `app/services/ai/data_health.py`

A lightweight service that aggregates quality scores across entities to give a domain-level readiness view.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_insight import AIInsight, AIInsightStatus, InsightDomain


@dataclass
class DomainHealthReport:
    domain: str
    total_entities_scanned: int
    avg_context_quality: float          # 0.0–1.0
    pct_above_threshold: float          # % of entities that would pass quality gate
    common_missing_fields: list[str]    # top 5 most-frequently-missing fields
    insight_count_completed: int
    insight_count_skipped: int
    avg_confidence_score: float | None  # from completed insights


class DataHealthService:
    """Aggregates context quality data from past insight runs to report domain readiness."""

    def domain_report(self, db: Session, domain: str, days: int = 30) -> DomainHealthReport:
        """Build a health report for one domain based on recent insight history."""
        from datetime import UTC, datetime, timedelta

        cutoff = datetime.now(UTC) - timedelta(days=days)
        base = db.query(AIInsight).filter(
            AIInsight.domain == InsightDomain(domain),
            AIInsight.created_at >= cutoff,
            AIInsight.context_quality_score.isnot(None),
        )

        # Aggregate quality scores
        stats = base.with_entities(
            func.count(AIInsight.id),
            func.avg(AIInsight.context_quality_score),
        ).first()
        total = stats[0] or 0
        avg_quality = float(stats[1] or 0)

        # Count above threshold (use 0.3 as a reasonable universal threshold)
        above = base.filter(AIInsight.context_quality_score >= 0.3).count()
        pct_above = (above / total * 100) if total > 0 else 0

        # Status breakdown
        completed = base.filter(AIInsight.status == AIInsightStatus.completed).count()
        skipped = base.filter(AIInsight.status == AIInsightStatus.skipped).count()

        # Average confidence of completed insights
        avg_conf_row = (
            base.filter(AIInsight.status == AIInsightStatus.completed)
            .with_entities(func.avg(AIInsight.confidence_score))
            .scalar()
        )
        avg_confidence = float(avg_conf_row) if avg_conf_row is not None else None

        # Common missing fields from skipped insights' structured_output
        missing_fields = self._aggregate_missing_fields(db, domain, cutoff)

        return DomainHealthReport(
            domain=domain,
            total_entities_scanned=total,
            avg_context_quality=round(avg_quality, 3),
            pct_above_threshold=round(pct_above, 1),
            common_missing_fields=missing_fields,
            insight_count_completed=completed,
            insight_count_skipped=skipped,
            avg_confidence_score=round(avg_confidence, 3) if avg_confidence else None,
        )

    def all_domains_report(self, db: Session, days: int = 30) -> list[DomainHealthReport]:
        return [self.domain_report(db, d.value, days) for d in InsightDomain]

    def _aggregate_missing_fields(self, db: Session, domain: str, cutoff) -> list[str]:
        """Extract top missing fields from skipped insights' structured_output."""
        skipped = (
            db.query(AIInsight.structured_output)
            .filter(
                AIInsight.domain == InsightDomain(domain),
                AIInsight.status == AIInsightStatus.skipped,
                AIInsight.created_at >= cutoff,
                AIInsight.structured_output.isnot(None),
            )
            .limit(200)
            .all()
        )
        field_counts: dict[str, int] = {}
        for (output,) in skipped:
            if isinstance(output, dict):
                for field in output.get("missing", []):
                    field_counts[field] = field_counts.get(field, 0) + 1

        return sorted(field_counts, key=field_counts.get, reverse=True)[:5]


data_health = DataHealthService()
```

---

## 7. API Endpoint

Add to `app/api/ai.py`:

```python
@router.get("/data-health")
def get_data_health(
    domain: str | None = None,
    days: int = 30,
    db: Session = Depends(get_db),
    auth=Depends(require_user_auth),
):
    """Domain data readiness report based on recent insight quality scores."""
    from app.services.ai.data_health import data_health

    if domain:
        report = data_health.domain_report(db, domain, min(days, 90))
        return {"reports": [_health_to_dict(report)]}
    reports = data_health.all_domains_report(db, min(days, 90))
    return {"reports": [_health_to_dict(r) for r in reports]}


def _health_to_dict(r) -> dict:
    return {
        "domain": r.domain,
        "total_entities_scanned": r.total_entities_scanned,
        "avg_context_quality": r.avg_context_quality,
        "pct_above_threshold": r.pct_above_threshold,
        "common_missing_fields": r.common_missing_fields,
        "insight_count_completed": r.insight_count_completed,
        "insight_count_skipped": r.insight_count_skipped,
        "avg_confidence_score": r.avg_confidence_score,
    }
```

---

## 8. Batch Scanner Integration

The existing batch scanners in `app/services/ai/context_builders/batch_scanners.py` find entities that need analysis. Update them to use quality scoring as a pre-filter:

```python
def _scan_tickets(db: Session, spec) -> list[tuple[str, str, dict]]:
    """Find tickets needing analysis, pre-filtered by data quality."""
    from app.services.ai.context_builders.tickets import score_ticket_context_quality

    candidates = _find_unanalyzed_tickets(db, spec)
    results = []
    for ticket in candidates:
        params = {"ticket_id": str(ticket.id)}
        quality = score_ticket_context_quality(db, params)
        if quality.sufficient:
            results.append(("ticket", str(ticket.id), params))
    return results
```

This prevents the engine from even attempting entities that would be skipped, saving the overhead of building full context strings for sparse entities during batch runs.

---

## 9. Tests — `tests/test_data_readiness.py`

```python
"""Tests for the data readiness layer: quality scoring, gating, and health reporting."""


class TestContextQualityScoring:
    """Test quality scorers return correct scores for varying data completeness."""

    def test_ticket_full_data_scores_high(self, db_session, ticket):
        """A ticket with all fields populated should score >= 0.7."""
        from app.services.ai.context_builders.tickets import score_ticket_context_quality
        result = score_ticket_context_quality(db_session, {"ticket_id": str(ticket.id)})
        assert result.score >= 0.7
        assert result.sufficient is True
        assert len(result.missing_fields) <= 2

    def test_ticket_missing_id_scores_zero(self, db_session):
        """Missing ticket_id param should score 0."""
        from app.services.ai.context_builders.tickets import score_ticket_context_quality
        result = score_ticket_context_quality(db_session, {})
        assert result.score == 0.0
        assert result.sufficient is False

    def test_ticket_skeleton_scores_low(self, db_session):
        """A ticket with only title/status (no description, comments, customer) should score low."""
        from app.models.tickets import Ticket
        from app.services.ai.context_builders.tickets import score_ticket_context_quality
        # Create minimal ticket
        ticket = Ticket(title="X", status=..., priority=..., channel=...)
        db_session.add(ticket)
        db_session.flush()
        result = score_ticket_context_quality(db_session, {"ticket_id": str(ticket.id)})
        assert result.score < 0.3
        assert "description" in result.missing_fields
        assert "comments" in result.missing_fields


class TestEngineQualityGating:
    """Test that the engine respects quality thresholds."""

    def test_skip_on_low_quality_creates_skipped_insight(self, db_session, mocker):
        """When quality is below threshold and skip_on_low_quality=True, engine creates a skipped insight."""
        # Mock the AI gateway so we can verify it's NOT called
        mock_generate = mocker.patch("app.services.ai.gateway.ai_gateway.generate_with_fallback")
        # ... invoke with a sparse entity ...
        # Assert: mock_generate not called, insight.status == "skipped"

    def test_proceed_on_low_quality_when_skip_disabled(self, db_session, mocker):
        """When skip_on_low_quality=False, engine calls LLM even with low quality."""
        # ... invoke with skip_on_low_quality=False persona ...
        # Assert: LLM was called, insight has context_quality_score persisted

    def test_quality_score_persisted_on_normal_insight(self, db_session, mocker):
        """Quality score is saved even on successful high-quality invocations."""
        # ... invoke normally ...
        # Assert: insight.context_quality_score is not None


class TestDataHealthService:
    """Test the domain health aggregation."""

    def test_domain_report_aggregates_correctly(self, db_session):
        """Health report should reflect actual quality distribution."""
        # Create mix of completed (high quality) and skipped (low quality) insights
        # Assert: avg_context_quality, pct_above_threshold, counts are correct

    def test_missing_fields_aggregation(self, db_session):
        """Common missing fields should be ranked by frequency."""
        # Create skipped insights with known missing field patterns
        # Assert: top fields match expected order
```

---

## 10. Implementation Order

This is designed as a **2-3 day sprint** inserted before or alongside Phase 2 of the Intelligence Engine plan.

### Day 1: Foundation
1. Add `ContextQualityResult` and `_default_quality_scorer` to `_base.py`
2. Add `context_quality_scorer`, `min_context_quality`, `skip_on_low_quality` to `PersonaSpec`
3. Add `context_quality_score` column + `skipped` enum value to `AIInsight` model
4. Write + run Alembic migration
5. Modify `engine.py` to call quality scorer and gate on threshold

### Day 2: Quality Scorers
6. Implement `score_ticket_context_quality()` in `context_builders/tickets.py`
7. Implement `score_inbox_context_quality()` in `context_builders/inbox.py`
8. Implement remaining 6 domain scorers (mechanical — same pattern)
9. Wire scorers + thresholds into each persona's `PersonaSpec`

### Day 3: Health Reporting + Tests
10. Create `app/services/ai/data_health.py`
11. Add `/data-health` endpoint to `app/api/ai.py`
12. Write `tests/test_data_readiness.py`
13. Run ticket analyst against 10-20 real tickets, review quality score distribution
14. Tune thresholds based on real data

---

## 11. What This Enables

Once this layer is in place:

- **Scheduled batch runs become self-regulating** — the engine only spends tokens on entities with enough data to produce useful insights. Sparse entities get `skipped` records that serve as a backlog of "needs more data."

- **The admin Intelligence dashboard** can show a data readiness heatmap: "Tickets: 85% ready, Campaigns: 40% ready, Vendors: 20% ready" — giving operators a clear signal of which domains to focus data entry on before turning on AI features.

- **Confidence trends become meaningful** — you can correlate `context_quality_score` with `confidence_score` to validate that higher data completeness actually produces better insights. If it doesn't, the persona's prompts need tuning.

- **Token budget is protected** — instead of burning 1,500 tokens on a skeleton ticket that produces a "not enough information to analyze" response, you spend 0 tokens and log a structured reason for the skip.

- **Progressive rollout** — start with `min_context_quality=0.0` (log quality but never block), review the distribution for a week, then raise thresholds to filter out the bottom 20% of entities per domain.
