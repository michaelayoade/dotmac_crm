# Ticket Rule-Based Assignment Design

## Goal

Implement rule-based ticket auto-assignment using reusable patterns from inbox routing, while keeping ticket-domain behavior and data model independent.

## Why

Current ticket assignment is mostly manual, with limited region-based manager/SPC defaults. We need deterministic, auditable, criteria-based routing for new tickets.

## Scope

- Rule matching on ticket attributes (priority, category/type, region, source/channel, service team, tags).
- Assignment strategies: `round_robin`, `least_loaded`.
- Optional team queue fallback when no eligible agent.
- Admin CRUD for rules + ordering/priority.
- Execution during ticket creation (and optional manual re-run).

## Non-Goals (Phase 1)

- Cross-tenant policy engine.
- ML-based assignment.
- Complex boolean expression builder UI.
- Historical rebalancing of already assigned tickets.

## Reuse Strategy From Inbox

### Reuse

1. Strategy split (`round_robin` vs `least_loaded`) and selector abstraction.
2. Availability-aware candidate filtering pattern.
3. Admin UX pattern for rule CRUD and enable/disable.

### Do Not Reuse Directly

1. `ConversationAssignment` and conversation load metrics.
2. Round-robin state in `CrmTeam.metadata`.
3. Inbox channel/keyword-specific rule shape.

## Proposed Data Model

### `ticket_assignment_rules` (new)

- `id` UUID PK
- `name` string
- `priority` int (higher first)
- `is_active` bool
- `match_config` JSONB
- `strategy` enum(`round_robin`, `least_loaded`)
- `team_id` UUID nullable (candidate pool/fallback queue)
- `assign_manager` bool default `false`
- `assign_spc` bool default `false`
- `created_at`, `updated_at`

`match_config` keys (Phase 1):
- `priorities: list[str]`
- `ticket_types: list[str]`
- `regions: list[str]`
- `sources: list[str]`
- `service_team_ids: list[str]`
- `tags_any: list[str]`

### `ticket_assignment_counters` (new)

- `id` UUID PK
- `rule_id` UUID FK unique
- `last_agent_id` UUID nullable
- `updated_at`

This avoids concurrency/race issues from metadata-based round-robin state.

## Matching Algorithm

1. Load active rules ordered by `priority DESC`, `created_at ASC`.
2. For each rule:
   - Evaluate `match_config` against ticket context.
   - Build candidate agent pool (active + team membership + eligibility).
   - Pick assignee by strategy.
   - Apply assignment and stop on first success.
3. If no agent selected and rule has `team_id`, optionally assign queue role fields only.

## Candidate Eligibility (Phase 1)

- Agent active.
- Optional team membership active.
- Optional presence status in `{online, away}`.
- Optional max open-ticket threshold.

Settings-driven toggles:
- `ticket_auto_assign_require_presence`
- `ticket_auto_assign_max_open_tickets`

## Load Metric for `least_loaded`

Count open ticket assignments (`new`, `open`, `pending`, `on_hold`) by `assigned_to_person_id`.

Tie-breakers:
1. Lowest count
2. Earliest agent `created_at`
3. Stable UUID ordering

## Execution Points

### Primary

On ticket create, when `assigned_to_person_id` is empty.

### Secondary

Manual admin action: “Run auto-assignment now”.

## Service Layout

- `app/services/ticket_assignment/rules.py` (CRUD + matching)
- `app/services/ticket_assignment/selectors.py` (strategy selectors)
- `app/services/ticket_assignment/engine.py` (orchestration)

Expose one entrypoint:
- `auto_assign_ticket(db, ticket_id, *, trigger, actor_person_id=None) -> AssignmentResult`

## API / Admin

- Admin page under ticket settings:
  - list rules
  - create/edit/delete
  - reorder priority
  - test rule against a ticket

- Optional API:
  - `POST /api/v1/tickets/{id}/auto-assign`

## Auditing

Emit assignment audit event with:
- matched rule id/name
- strategy
- candidate count
- selected assignee (or fallback reason)

## Migration Plan

1. Add new tables + enums.
2. Backfill optional defaults from existing region assignment map if desired.
3. Add settings keys with conservative defaults.
4. Ship feature-flagged: `ticket_auto_assignment_enabled=false` initially.

## Tests

### Unit

- Rule matcher coverage for each `match_config` key.
- Selector behavior for both strategies.
- Round-robin counter correctness.

### Integration

- Ticket create with no assignee routes correctly.
- Pre-assigned ticket is not overridden.
- No candidates -> expected fallback/no-op.
- Priority ordering honored.

### Concurrency

- Simulated parallel assignment requests do not duplicate round-robin pointer movement incorrectly.

## Rollout

1. Deploy schema + disabled feature flag.
2. Enable in staging with debug logging.
3. Validate assignment distribution + audit trail.
4. Enable production for one team, then broaden.

## Immediate Follow-Up (Inbox Hardening)

To keep behavior consistent across inbox and ticket routing:
1. Add explicit `priority` to inbox routing rules.
2. Add runtime tests for `apply_routing_rules`.
3. Consider migrating inbox RR state from `team.metadata` to dedicated table later.
