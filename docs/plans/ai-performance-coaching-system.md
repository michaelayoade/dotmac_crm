# AI-Powered Agent Performance & Coaching System

## Context

DotMac CRM tracks rich activity data across tickets, projects, work orders, CRM inbox, sales, and contacts — but has no unified way for management to review staff performance or for agents to track their own improvement. This feature adds an AI-powered "Management LLM" that scores all staff across every domain, generates coaching recommendations via a pluggable LLM provider (OpenAI-compatible API, e.g. DeepSeek or self-hosted vLLM), and provides dashboards for both managers and agents.

## Design Decisions

- **Scope**: All staff with tracked activity (not just CRM agents) — technicians, project managers, sales reps, support agents
- **Hierarchy**: Use existing `ServiceTeam.manager_person_id` + `ServiceTeamMember.role` (member/lead/manager) synced from DotMac ERP — no new hierarchy needed
- **Scoring**: Role-adaptive — weight domains by `ServiceTeam.team_type` plus activity-profile overrides for sales-heavy roles
- **Idempotency**: Scoring/review jobs are safe to retry (upsert by unique period keys, no duplicate rows)
- **AI Reviews**: Weekly auto-generation for flagged agents (below threshold) + on-demand by managers
- **Visibility**: Full self-service via existing agent route family (`/agent/*`) plus manager/admin views in `/admin/*`
- **Security**: API query params never widen data access; server-side scope enforcement always uses auth person + managed-team membership
- **Permissions**: Reuse existing permission families (`reports:*`, `crm:*`, `system:*`) instead of introducing a new `performance:*` namespace

## File Structure (New Files)

```
app/
├── models/performance.py                      # Models + enums
├── schemas/performance.py                     # Pydantic schemas
├── services/performance/
│   ├── __init__.py
│   ├── scoring.py                             # Score calculation per domain
│   ├── reviews.py                             # AI review generation orchestrator
│   ├── reports.py                             # Dashboard queries (leaderboard, trends, peer comparison)
│   └── goals.py                               # Goal CRUD + progress
├── services/ai/
│   ├── __init__.py
│   ├── client.py                              # OpenAI-compatible LLM client (vLLM)
│   └── prompts/
│       ├── __init__.py
│       └── performance_review.py              # Prompt templates
├── tasks/performance.py                       # Celery: weekly scores + AI reviews
├── web/admin/performance.py                   # Web routes (manager/admin)
├── web/agent/performance.py                   # Web routes (self-service)
└── api/performance.py                         # JSON API endpoints

templates/admin/performance/
├── team_overview.html                         # Manager: team leaderboard
├── agent_detail.html                          # Manager: individual drilldown
├── review_detail.html                         # AI review full view
├── _leaderboard_table.html                    # HTMX partial: sortable agent table
├── _score_cards.html                          # HTMX partial: domain score cards
└── _trend_chart_data.html                     # HTMX partial: chart data endpoint

templates/agent/performance/
└── my_scorecard.html                          # Agent self-service
```

## Files to Modify

| File | Change |
|------|--------|
| `app/models/__init__.py` | Import `performance` models |
| `app/models/domain_settings.py` | Add `performance` to `SettingDomain` enum |
| `app/services/settings_spec.py` | Add performance + AI integration settings |
| `app/services/scheduler_config.py` | Register weekly scoring + review tasks |
| `app/web/admin/__init__.py` | Register performance router |
| `app/web/agent/__init__.py` | Register agent performance router |
| `app/main.py` | Include performance API router (`_include_api_router(performance_router, ...)`) |
| `templates/components/navigation/admin_sidebar.html` | Add Performance links to Reports section |
| `pyproject.toml` | Ensure `httpx` is available for provider API calls |

## Data Model

### `AgentPerformanceScore` (weekly snapshots)

```python
class PerformanceDomain(enum.Enum):
    support = "support"           # Tickets: SLA, resolution time, escalation
    operations = "operations"     # Projects/tasks: completion, on-time, effort accuracy
    field_service = "field_service"  # Work orders: completion, schedule adherence, documentation
    communication = "communication"  # Inbox: FRT, resolution time, volume, channel coverage
    sales = "sales"               # Leads: conversion, pipeline value, quote acceptance
    data_quality = "data_quality" # Contact completeness, tagging, note thoroughness

# Columns:
id: UUID PK
person_id: UUID FK(people.id)
score_period_start: DateTime(tz)
score_period_end: DateTime(tz)
domain: PerformanceDomain
raw_score: Numeric(5,2)          # 0-100 domain score
weighted_score: Numeric(5,2)     # After weight applied
metrics_json: JSON               # Raw metric values for drilldown
created_at: DateTime(tz)

# Constraints/Indexes:
# - UNIQUE(person_id, score_period_start, domain)    # idempotent score writes
# - INDEX(person_id, score_period_start), INDEX(domain), INDEX(score_period_start)
```

### `AgentPerformanceSnapshot` (one row per person per period)

```python
id: UUID PK
person_id: UUID FK(people.id)
score_period_start: DateTime(tz)
score_period_end: DateTime(tz)
composite_score: Numeric(5,2)      # 0-100
domain_scores_json: JSON           # {"support": 72.5, ...}
weights_json: JSON                 # Effective weights applied for this run
team_id: UUID FK(service_teams.id) nullable
team_type: String(40) nullable
sales_activity_ratio: Numeric(8,4) nullable
created_at: DateTime(tz)
updated_at: DateTime(tz)

# Constraints/Indexes:
# - UNIQUE(person_id, score_period_start, score_period_end)
# - INDEX(score_period_start), INDEX(composite_score), INDEX(team_id, score_period_start)
```

### `AgentPerformanceReview` (AI-generated coaching)

```python
id: UUID PK
person_id: UUID FK(people.id)
review_period_start: DateTime(tz)
review_period_end: DateTime(tz)
composite_score: Numeric(5,2)
domain_scores_json: JSON         # {domain: score} snapshot
summary_text: Text               # AI-generated summary
strengths_json: JSON             # ["strength 1", ...]
improvements_json: JSON          # ["area 1", ...]
recommendations_json: JSON       # [{priority, category, recommendation, expected_impact}]
callouts_json: JSON              # [{type, reference, observation}]
llm_model: String(100)
llm_tokens_in: Integer
llm_tokens_out: Integer
is_acknowledged: Boolean(default=False)
acknowledged_at: DateTime(tz) nullable
created_at: DateTime(tz)

# Constraints/Indexes:
# - UNIQUE(person_id, review_period_start, review_period_end)   # idempotent auto-review writes
# - INDEX(person_id, review_period_start), INDEX(is_acknowledged)
```

### `AgentPerformanceGoal` (target tracking)

```python
class GoalStatus(enum.Enum):
    active = "active"
    achieved = "achieved"
    missed = "missed"
    canceled = "canceled"

id: UUID PK
person_id: UUID FK(people.id)
domain: PerformanceDomain
metric_key: String(80)           # e.g. "avg_first_response_minutes", "sla_compliance_rate"
label: String(200)               # Human-readable: "Reduce FRT to under 10 minutes"
target_value: Numeric(12,2)
current_value: Numeric(12,2) nullable
comparison: String(10)           # "lte" | "gte" — is lower or higher better?
deadline: Date
status: GoalStatus
created_by_person_id: UUID FK nullable
created_at, updated_at: DateTime(tz)

# Indexes: (person_id, status), (deadline)
```

## Scoring Logic

### Domain Score Formulas (each 0-100)

All formulas use safe math guards:
- `safe_div(num, den)` returns `0` if `den <= 0` or null
- Team-average comparison metrics return neutral midpoint when team baseline unavailable (`team_avg <= 0` → half-points for that metric)
- Every metric is clamped to `[0, metric_max_points]`

**Support** (tickets):
- SLA compliance rate: 40pts — `met / total * 40`
- Avg resolution time vs team avg: 30pts — `30 * max(0, 1 - agent/team_avg)`
- Escalation rate (lower=better): 20pts — `20 * (1 - rate)`
- CSAT from ticket-closed surveys: 10pts — `avg_rating / 5 * 10`

**Operations** (projects/tasks):
- Task completion rate: 35pts — `done / assigned * 35`
- On-time delivery rate: 30pts — `on_time / total * 30`
- Effort accuracy (actual vs estimate): 20pts — `20 * max(0, 1 - |actual-est|/est)`
- Blocked task rate (lower=better): 15pts — `15 * (1 - blocked/total)`

**Field Service** (work orders):
- Completion rate: 30pts — `completed / assigned * 30`
- Schedule adherence: 25pts — `25 * max(0, 1 - avg_delay_mins/60)`
- Duration accuracy: 25pts — `25 * max(0, 1 - |actual-est|/est)`
- Documentation rate (has notes): 20pts — `with_notes / total * 20`

**Communication** (inbox):
- First Response Time vs team avg: 30pts — `30 * max(0, 1 - agent_frt/team_frt)`
- Resolution time vs team avg: 25pts — same pattern
- Conversations resolved: 25pts — `min(25, agent_vol/team_avg * 25)`
- Channel coverage: 20pts — `channels_used / channels_available * 20`

**Sales** (leads/quotes):
- Win rate: 35pts — `won / (won+lost) * 35`
- Pipeline value vs team avg: 25pts — `min(25, agent/team_avg * 25)`
- Quote acceptance rate: 25pts — `accepted / sent * 25`
- Activity count (msgs to leads): 15pts — `min(15, agent/team_avg * 15)`

**Data Quality** (contacts/records):
- Contact completeness: 40pts — avg % of key fields filled across records agent touched
- Organization completeness: 25pts — same for org records
- Tagging discipline: 20pts — % of tickets/conversations with tags
- Note thoroughness: 15pts — avg note length threshold (>100 chars = full score)

### Composite Score

```
composite = safe_div(sum(domain_score * weight), sum(weights))
```

Persist composite into `AgentPerformanceSnapshot` for each agent/period using upsert semantics.
`generate_flagged_reviews` reads from snapshot table only (no ad-hoc per-request aggregation).

### Role-Adaptive Weights (by `ServiceTeam.team_type` + profile override)

| Domain | operations | support | field_service | Default |
|--------|-----------|---------|---------------|---------|
| support | 15% | 30% | 10% | 20% |
| operations | 25% | 10% | 15% | 15% |
| field_service | 20% | 5% | 35% | 15% |
| communication | 15% | 30% | 10% | 20% |
| sales | 15% | 15% | 20% | 20% |
| data_quality | 10% | 10% | 10% | 10% |

Weights configurable via `SettingDomain.performance` → `domain_weights` (JSON). Team-type overrides via `domain_weights_operations`, `domain_weights_support`, `domain_weights_field_service`.

For sales-heavy staff who are not in a dedicated sales team type, apply optional profile override:
- `domain_weights_sales_profile` when `sales_activity_ratio >= sales_profile_min_ratio` (default `0.5`)
- `sales_activity_ratio = sales_events / total_tracked_events` for the scoring window

### Scoring Queries (batch-optimized)

All scores computed in batch per scoring period — one query per metric across ALL agents, then distributed. No N+1 patterns. Example:

```python
# Batch FRT for all agents in one query
frt_by_agent = (
    db.query(
        ConversationAssignment.agent_id,
        func.avg(extract_epoch_diff(first_outbound.sent_at, first_inbound.received_at) / 60)
    )
    .filter(...)
    .group_by(ConversationAssignment.agent_id)
).all()
```

## AI Review Generation

### Flow

1. Celery task runs weekly → computes scores for all active staff
2. Identifies flagged agents: composite score < threshold (default 70)
3. For each flagged agent (+ any manually triggered):
   a. Load scorecard + domain breakdowns
   b. Sample 3 recent tickets, 3 conversations, 2 work orders (most recent resolved/completed) using redacted summaries only
   c. Build structured prompt with metrics + activity samples
   d. Call configured LLM provider API with timeout + bounded retries → parse JSON response
   e. Store `AgentPerformanceReview` record
   f. Log to `AuditEvent`
4. If retries fail, mark run record as failed with reason; do not block other agents in the batch

### LLM Provider Integration

```python
# app/services/ai/client.py
class AIResponse(TypedDict):
    content: str
    tokens_in: int | None
    tokens_out: int | None
    model: str
    provider: str

def build_ai_client(db) -> VllmClient:
    # Reads SettingDomain.integration keys (vllm_base_url, vllm_model, etc).
    ...
```

Settings are provider-agnostic with provider-specific keys in `SettingDomain.integration`.

### Prompt Structure

System prompt establishes the coaching persona + JSON output schema.
User prompt contains: period, composite score, domain scores table, metric details, activity samples (ticket/work-order/conversation summaries after redaction).

### Data Minimization & Privacy Controls

- Do not send full raw message/comment bodies to the LLM
- Redact PII/secrets before prompt construction: emails, phone numbers, tokens, account IDs, addresses, payment references
- Include only minimum excerpts needed for coaching evidence (max N chars per sample, configurable)
- Persist only final structured review output + token counts; do not persist full prompt/response transcript
- Gate auto-review behind `review_generation_enabled` and LLM provider configuration (base URL + model)
- Log AI review generation via `log_audit_event()` with actor + target person + model metadata (no prompt payload)

Output schema:
```json
{
  "summary": "2-3 sentence overall assessment",
  "strengths": ["evidence-based strength..."],
  "improvements": ["specific area with metric + target..."],
  "recommendations": [
    {"priority": "high", "category": "response_time", "action": "...", "impact": "..."}
  ],
  "callouts": [
    {"type": "positive|concern", "reference": "Ticket #1234", "observation": "..."}
  ]
}
```

### Cost Estimate

~2,300 tokens/review (1,500 in + 800 out). Costs depend on:
- Self-hosted vLLM: infrastructure (GPU/CPU), model size, and utilization
- Hosted APIs: provider pricing for input/output tokens

## Routes & Templates

### Web Routes (`app/web/admin/performance.py`)

```
GET  /admin/performance                      → team_overview.html (redirect based on role)
GET  /admin/performance/team                 → team_overview.html (manager: leaderboard)
GET  /admin/performance/team/_table          → _leaderboard_table.html (HTMX partial)
GET  /admin/performance/agents/{person_id}   → agent_detail.html (manager: drilldown)
GET  /admin/performance/agents/{person_id}/_scores → _score_cards.html (HTMX partial)
GET  /admin/performance/reviews/{review_id}  → review_detail.html (full AI review)
POST /admin/performance/agents/{person_id}/generate-review → trigger on-demand review (303 redirect)
```

Router/dependency pattern (align with current admin style):

```python
# app/web/admin/performance.py
router = APIRouter(prefix="/performance", tags=["admin-performance"])

@router.get("/team", response_class=HTMLResponse, dependencies=[Depends(require_permission("reports:operations"))])
def team_overview(...): ...

@router.get(
    "/agents/{person_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("reports:operations"))],
)
def agent_detail(...): ...
```

### Web Routes (`app/web/agent/performance.py`)

```
GET  /agent/my-performance                   → my_scorecard.html (agent self-service)
GET  /agent/my-performance/_scores           → _score_cards.html (HTMX partial)
GET  /agent/my-performance/reviews/{id}      → review_detail.html (own review only)
POST /agent/my-performance/reviews/{id}/ack  → acknowledge review (303 redirect)
```

Self-service permission pattern:

```python
# app/web/agent/performance.py
roles = current_user.get("roles") or []
scopes = current_user.get("permissions") or []
if not can_view_inbox(roles, scopes):
    raise HTTPException(status_code=403, detail="Forbidden")
```

### API Routes (`app/api/performance.py`)

```
GET  /api/v1/performance/scores?person_id=&period=     → Score history (manager/admin may pass person_id)
GET  /api/v1/performance/reviews?person_id=&limit=     → Review list (manager/admin may pass person_id)
POST /api/v1/performance/reviews/generate               → Trigger review
GET  /api/v1/performance/goals?person_id=               → Goals list (manager/admin may pass person_id)
POST /api/v1/performance/goals                          → Create goal
PATCH /api/v1/performance/goals/{id}                    → Update goal
GET  /api/v1/performance/peer-comparison?period=        → Anonymized comparison
GET  /api/v1/performance/team-summary?team_id=&period=  → Team aggregate
```

### Access Control

| Route | Who Can Access |
|-------|---------------|
| `/admin/performance/team` | Users with `reports:operations`/`reports` (or admin) AND team-manager/lead scope check |
| `/admin/performance/agents/{id}` | Same as above, scoped to their team members |
| `/agent/my-performance` | Users passing `can_view_inbox(roles, scopes)` (or admin), self only |
| Generate review | Team managers + admins only |
| Goal create/update | Team managers for their members, self for own goals |
| `/api/v1/performance/*` | Enforced by auth scope + managed-person filter; `person_id` ignored unless requester can manage that person |

Implementation details:
- `_get_managed_person_ids(db, current_user)` must include only active teams/members (`ServiceTeam.is_active = true`, `ServiceTeamMember.is_active = true`)
- Manager scope = teams where `manager_person_id = current_user.person_id` OR current user is active `lead/manager` member
- API handlers must resolve an `effective_person_id` server-side:
  - self requester → own `person_id`
  - manager/admin requester + valid managed target → requested `person_id`
  - otherwise → `403 Forbidden`
- For manager/admin routes and privileged API endpoints, use `Depends(require_permission("reports:operations"))`
- For broad read/report routes, accept inherited hierarchy (`reports:operations` or `reports`) via existing permission expansion behavior

### Template Design

**Team Overview** (`team_overview.html`):
- Page header: `page_header(title="Team Performance", color="cyan", color2="blue")`
- Filter bar: team selector, period selector (this week / last week / last month / custom)
- 4 stats cards: Team Avg Score, Top Performer, Needs Coaching count, Reviews Generated
- Leaderboard table: agent name, composite score (color-coded), sparkline trend, domain mini-bars, actions (view / generate review)
- Chart: team score distribution histogram

**Agent Detail** (`agent_detail.html`):
- Detail header with agent name, title, team, avatar
- Score trend line chart (12 weeks, Chart.js via `DotmacCharts.createLineChart`)
- Domain radar chart (6 axes, current vs previous period)
- Domain score cards grid (6 cards with metric breakdowns)
- Goals section with progress bars
- Recent reviews timeline (last 4, expandable summaries)
- Activity highlights: best/worst metric callouts

**My Scorecard** (`my_scorecard.html`):
- Same layout as agent detail but for current user
- Peer comparison section: percentile rank, team avg, score distribution histogram (anonymized)
- Unacknowledged reviews banner with CTA
- Goal self-tracking

**Review Detail** (`review_detail.html`):
- AI summary in a highlighted card
- Strengths list (green badges)
- Improvements list (amber badges)
- Recommendations table (priority-sorted, with category + expected impact)
- Activity callouts (positive = green border, concern = amber border)
- Acknowledge button (agent view only)

### Sidebar Navigation

Add to **Reports** section in `admin_sidebar.html`:

```
{% set can_performance_team = is_admin
   or 'reports:operations' in user_permissions
   or 'reports' in user_permissions %}

Reports section:
  - [existing items...]
  - My Performance    → /agent/my-performance         (all authenticated staff)
  - Team Performance  → /admin/performance/team      (managers + admins only)
```

Update `section_for_page`: `'my-performance': 'reports'`, `'team-performance': 'reports'`

## Settings (`settings_spec.py`)

New domain: `SettingDomain.performance`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `scoring_enabled` | boolean | `true` | Master toggle for score computation |
| `review_generation_enabled` | boolean | `false` | AI review auto-generation toggle |
| `domain_weights` | json | `{"support":20,"operations":15,...}` | Default weight % per domain |
| `domain_weights_operations` | json | `null` | Override weights for operations teams |
| `domain_weights_support` | json | `null` | Override weights for support teams |
| `domain_weights_field_service` | json | `null` | Override weights for field service teams |
| `domain_weights_sales_profile` | json | `null` | Optional override weights for sales-heavy activity profiles |
| `sales_profile_min_ratio` | decimal | `0.5` | Min sales activity ratio to apply sales profile weights |
| `flagged_threshold` | integer | `70` | Score below this triggers auto-review |
| `peer_comparison_min_team_size` | integer | `3` | Min team size for peer comparison (privacy) |
| `review_sample_tickets` | integer | `3` | Tickets to sample for AI review context |
| `review_sample_conversations` | integer | `3` | Conversations to sample |
| `review_sample_work_orders` | integer | `2` | Work orders to sample |
| `review_sample_max_chars` | integer | `600` | Max characters per sampled activity after redaction |
| `max_reviews_per_run` | integer | `20` | Cap generated reviews per scheduled run |
| `review_manual_daily_limit_per_manager` | integer | `25` | Per-manager cap for manual review triggers/day |
| `review_cooldown_hours` | integer | `24` | Min hours between reviews for same person/period unless forced by admin |

Add to `SettingDomain.integration` (if not already there):

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `llm_provider` | string | `vllm` | Provider selector (currently `vllm`) |
| `vllm_base_url` | string | `null` | OpenAI-compatible base URL (example: `http://vllm:8000/v1`) |
| `vllm_model` | string | `null` | Model ID served by vLLM |
| `vllm_api_key` | string (secret) | `null` | Optional API key (if your gateway requires one) |
| `vllm_timeout_seconds` | integer | `30` | API timeout per request |
| `vllm_max_retries` | integer | `2` | Retry attempts for transient failures |
| `vllm_max_tokens` | integer | `2048` | Max completion tokens |

## Celery Tasks (`app/tasks/performance.py`)

### `compute_weekly_scores`
- Schedule: weekly cron (Monday, UTC) via scheduler interval settings equivalent to cron cadence
- Logic: For each active person with any tracked activity in the period → compute all 6 domain scores → upsert `AgentPerformanceScore` rows + upsert one `AgentPerformanceSnapshot`
- Reliability: include checkpoint table/key (`last_scored_period_end`) so missed weeks are backfilled on next successful run
- Batch-optimized: one query per metric across all agents

### `generate_flagged_reviews`
- Schedule: chained from `compute_weekly_scores` for same scoring period
- Logic: Query `AgentPerformanceSnapshot` where `composite_score < flagged_threshold` and no review exists for period → generate AI review
- Rate-limited: max `max_reviews_per_run` reviews per run, remainder queued for next run
- Only runs if `review_generation_enabled` setting is true
- Idempotent: insert with unique `(person_id, review_period_start, review_period_end)` guard

### `update_goal_progress`
- Schedule: interval task every `86400` seconds (24 hours)
- Logic: For each active goal → recompute `current_value` from latest score → check if achieved or missed deadline

## Reuse Existing Code

| Need | Existing Code to Reuse |
|------|----------------------|
| Ticket metrics | `app/services/crm/reports.py` → `ticket_support_metrics()` |
| Inbox FRT/resolution | `app/services/crm/reports.py` → `agent_performance_metrics()`, `inbox_kpis()` |
| Sales metrics | `app/services/crm/reports.py` → `agent_sales_performance()`, `sales_pipeline_metrics()` |
| Field service metrics | `app/services/crm/reports.py` → `field_service_metrics()` |
| Project metrics | `app/services/crm/reports.py` → `project_metrics()` |
| Audit logging | `app/services/audit_helpers.py` → `log_audit_event()` |
| Settings access | `app/services/domain_settings.py` → `resolve_value()`, `resolve_values_atomic()` |
| Chart.js helpers | `static/js/charts.js` → `DotmacCharts.createLineChart()`, `createDoughnutChart()` |
| CSV export | `app/web/admin/reports.py` → `_csv_response()` pattern |
| Page context | `build_admin_context()` pattern from other admin routes |
| Agent auth scope check | `app/web/agent/reports.py` + `app/services/crm/inbox/permissions.py` → `can_view_inbox()` |
| UI macros | `templates/components/ui/macros.html` → `page_header`, `stats_card`, `data_table`, `filter_bar`, `status_badge`, `tabs` |

## Router Wiring (Exact)

`app/web/admin/__init__.py`:
```python
from app.web.admin.performance import router as performance_router
...
router.include_router(performance_router)
```

`app/web/agent/__init__.py`:
```python
from app.web.agent.performance import router as performance_router
...
router.include_router(performance_router)
```

`app/main.py`:
```python
from app.api.performance import router as performance_router
...
_include_api_router(performance_router, dependencies=[Depends(require_user_auth)])
```

## Implementation Order

### Phase 1: Models + Migration
1. Create `app/models/performance.py` with all 4 models + enums (`AgentPerformanceSnapshot` included)
2. Add `performance` to `SettingDomain` enum
3. Add settings to `settings_spec.py`
4. Ensure `httpx` dependency is present in `pyproject.toml`
5. Generate Alembic migration (including unique constraints for score/review idempotency)
6. Register models in `app/models/__init__.py`

### Phase 2: Scoring Engine
7. Create `app/services/performance/scoring.py` — all 6 domain calculators + composite + safe math guards + snapshot upsert
8. Create `app/tasks/performance.py` — `compute_weekly_scores` task
9. Register task in `scheduler_config.py` with weekly cadence + backfill checkpoint handling
10. **Test**: Run scoring manually, verify scores in DB

### Phase 3: Reports Service + API
11. Create `app/services/performance/reports.py` — leaderboard, trends, peer comparison queries
12. Create `app/services/performance/goals.py` — goal CRUD
13. Create `app/schemas/performance.py` — Pydantic schemas
14. Create `app/api/performance.py` — JSON API endpoints
15. Register router in `app/main.py` via `_include_api_router(...)`

### Phase 4: Management Dashboard
16. Create `app/web/admin/performance.py` — web routes
17. Create `templates/admin/performance/team_overview.html` + HTMX partials
18. Create `templates/admin/performance/agent_detail.html` with Chart.js
19. Update sidebar navigation
20. **Test**: Manager can view team scores, drill into agents, see charts

### Phase 5: Agent Self-Service
21. Create `app/web/agent/performance.py` + `templates/agent/performance/my_scorecard.html`
22. Add peer comparison + goal tracking UI
23. **Test**: Agent can view own scores, compare anonymously, track goals

### Phase 6: AI Reviews
24. Create `app/services/ai/client.py` — OpenAI-compatible client/factory (`vllm`)
25. Create `app/services/ai/prompts/performance_review.py` — prompt templates
26. Create `app/services/performance/reviews.py` — review orchestrator + redaction pipeline
27. Create `templates/admin/performance/review_detail.html`
28. Add `generate_flagged_reviews` Celery task
29. **Test**: Generate review for test agent, verify quality, test acknowledge flow

### Phase 7: Polish
30. Goal progress auto-update task
31. CSV export for leaderboard
32. Permission gating end-to-end test
33. Dark mode verification on all templates

## Verification

After each phase:

1. **Syntax check**: `python3 -c "import ast; ast.parse(open('file').read())"` for each new Python file
2. **App boot**: `docker compose restart app` → `docker compose logs app --tail=50` — no tracebacks
3. **API endpoint test**: Fresh JWT → curl each new `/api/v1/performance/*` route → expect 200/201
4. **Web route test**: authenticated session + CSRF token for POST routes (`/admin/performance/*`) → expect 200/303
5. **HTMX partials**: Test each `hx-get` independently → 200
6. **Scoring accuracy**: Manually verify 2-3 agent scores against raw data queries
7. **AI review quality**: Read generated reviews for factual accuracy vs actual metrics
8. **Access control**: Verify agent cannot see other agents' scores, manager sees only their team (scoped by `person_id`, not auth `user.id`)
9. **Idempotency**: Re-run scoring/review tasks for same period and verify no duplicate rows are created
10. **Recovery**: Simulate a missed weekly run and verify next run backfills missed period(s)
