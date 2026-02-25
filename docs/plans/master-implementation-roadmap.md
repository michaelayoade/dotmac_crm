# DotMac Omni Master Implementation Roadmap

**Date:** 2026-02-25  
**Purpose:** Single source of truth for all planned feature work.

## Principles

1. Prioritize customer-facing workflow wins first (Inbox, Tickets, Reporting).
2. Ship in thin vertical slices (DB + service + UI + audit + tests per feature).
3. Keep all automation deterministic and auditable.
4. Gate risky capabilities behind settings/feature flags.

## Phase 1 (Weeks 1-3): Support Operations Core

### Inbox
- Conversation priority (urgent/high/medium/low) with filters/sorting.
- Full snooze scheduler (1h, tomorrow, next week, custom, next reply).
- Bulk actions (assign, status, labels, priority).
- Conversation mute and notification suppression.
- Email CC/BCC on outbound replies.
- Saved filters/folders in sidebar.

### Ticketing
- Complete ticket rule assignment admin CRUD (create/edit/delete/enable/reorder/test).
- Add candidate guard settings:
  - `ticket_auto_assign_require_presence`
  - `ticket_auto_assign_max_open_tickets`
- Enhance assignment audit payload:
  - matched rule name/id
  - strategy
  - candidate count
  - selected assignee or failure reason
- Add queue fallback behavior when no assignee is eligible.

### SLA & Support Metrics
- SLA policy assignment by rule/automation.
- SLA reports: breach rate, breach reasons, hit/miss by queue/agent.
- Team and channel support dashboards.

## Phase 2 (Weeks 4-6): Agent Productivity + Automation

### Productivity
- Inbox macros (multi-step actions).
- Template shortcodes (`/shortcut`) in composer.
- Keyboard shortcuts and command palette.
- Collision detection (who is viewing/replying).
- Typing indicators where channel supports it.

### Automation
- Event-condition-action automation expansion:
  - auto resolve after idle period
  - assignment escalation chains
  - delayed actions
- Label metadata (color + management UX + analytics dimensions).

## Phase 3 (Weeks 7-9): Intelligence + Data Readiness

### Intelligence Engine Completion
- Complete persona coverage and production hardening.
- Migrate legacy AI use cases to engine wrappers.
- Full insight lifecycle actions: acknowledge/action/expire.

### Data Readiness Layer Completion
- Implement data health service and endpoint:
  - `app/services/ai/data_health.py`
  - `/api/v1/ai/data-health`
- Domain readiness dashboards and trend tracking.
- Complete scorer parity across all persona domains.

### AI Performance Coaching
- Weekly score generation + manager review workflows.
- Admin team leaderboard and agent drilldowns.
- Agent self-service scorecard and coaching goals.

## Phase 4 (Weeks 10-12): Reporting + Analytics Platform

- Unified reporting framework across inbox, tickets, sales, projects, and dispatch.
- Exportable reports (CSV first, then PDF/Excel).
- Scheduled report delivery.
- KPI target tracking with variance visuals.
- Funnel and cohort analytics for sales pipeline.
- Alerting for anomalies and trend breaks.

## Phase 5 (Weeks 13-16): Integrations + Platform Maturity

### Channels & Integrations
- Bidirectional SMS inbox channel.
- Slack/Teams integration (notifications first, reply bridge second).
- Webhook management UX.
- Contact import/export and dedup workflows.

### Security & Governance
- Comprehensive record-level audit coverage.
- Admin MFA and optional SSO.
- Field-level visibility controls for sensitive data.
- Data governance tools (consent tracking, retention controls).

## Cross-Cutting Requirements (Every Phase)

- API + web parity for new capabilities.
- Permission checks for all entry points.
- PRG (`303`) and HTMX compatibility for admin forms.
- Dark-mode compatible UI updates.
- Telemetry + audit events for every state-changing action.
- Tests:
  - unit tests for rules/engines/selectors
  - integration tests for service and API wiring
  - critical UI flow tests for admin inbox/tickets

## Delivery Cadence

1. Weekly planning: lock scope for one phase slice.
2. Mid-week release candidate in staging.
3. End-week production deploy with feature flags where needed.
4. Weekly roadmap update in this file only.

## Definition of Done (Per Feature)

- Data model/migration complete (if required).
- Service-layer logic implemented with audit coverage.
- Admin/API endpoints shipped with permissions.
- UI/UX complete (including dark mode).
- Tests added and passing in CI.
- Documentation updated in user/developer guides.

## Data Cleanliness Implementation Plan (Execution Track)

### Goal
- Keep operational and analytical data trustworthy by preventing dirty writes, deduping ingest, and detecting drift early.

### Milestone 1: Baseline and Risk Inventory (2 days)
- [x] Capture baseline metrics from:
  - `/api/v1/ai/data-health`
  - `/api/v1/ai/data-health/trend`
  - `/admin/intelligence/readiness`
- [x] Rank top data quality risks by impact:
  - inbox ingest
  - ticket assignment
  - admin manual edits
  - imports/sync pipelines
- [x] Publish initial “top missing fields” report per domain.

### Milestone 2: Write-Path Guardrails (3 days)
- [ ] Audit service-layer write paths and remove direct route/model writes where present.
- [ ] Standardize normalization before writes:
  - email, phone, external IDs, channel identifiers
- [ ] Enforce schema validation and reject invalid state transitions.
- [ ] Add unit tests for normalization + invalid payload rejection.

### Milestone 3: DB Invariants and Idempotency (5 days)
- [ ] Add safe DB constraints/migrations:
  - `NOT NULL`, `CHECK`, `UNIQUE`, FK coverage on critical fields
- [ ] Strengthen dedupe keys for inbound/webhook/import entities.
- [ ] Ensure idempotent upsert behavior for external events.
- [ ] Add regression tests for duplicate-event replay and constraint protection.

### Milestone 4: Monitoring and Alerting (3 days)
- [ ] Add threshold-based drift monitoring:
  - avg quality drop
  - skipped/failed AI spike
  - fallback-heavy assignment patterns
- [ ] Define alert thresholds and response owners.
- [ ] Add weekly trend snapshots for leadership review.

### Milestone 5: Audit Coverage and Ops Checks (2 days)
- [ ] Verify audit events for all state-changing operations:
  - assignment, status changes, automation actions, AI lifecycle
- [ ] Add scheduled integrity checks:
  - orphan detection
  - invalid enum/state combinations
  - inactive-linked record anomalies
- [ ] Document remediation flow for failed checks.

### Milestone 6: CI Gates and SOP (2 days)
- [ ] Enforce quality pipeline in CI:
  - `ruff check app/ tests/ --fix`
  - `ruff format app/ tests/`
  - `mypy app/`
  - `bandit -r app -q`
  - `pytest` (targeted + critical suites)
- [ ] Publish runbook with:
  - daily checks
  - weekly audit routine
  - incident response + rollback guidance

### Success Criteria
- Dirty-write incidents trend toward zero for two consecutive weeks.
- Duplicate ingest incidents are blocked by idempotency tests and constraints.
- Data health trend is stable/improving across all domains.
- All critical mutations are traceable through audit logs.
