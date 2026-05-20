# Production DB Session Rollout Checklist

## Objective

Reduce interactive latency and eliminate app-side database session saturation without destabilizing the live production system.

## Verified Current State

- [x] Verify the slowdown against live production logs.
- [x] Verify the live application database target from the running container.
- [x] Verify the slowdown mechanism from live Postgres session state.
- [x] Confirm this is app-side pool/session pressure, not Postgres `max_connections` exhaustion.

## Verified Root Cause Summary

- The live app is saturating its own SQLAlchemy pool under concurrent request load.
- Long-running web requests hold DB sessions for too long.
- Some request-time reads are left `idle in transaction`, which extends pool occupancy.
- Heavy report workloads compete with interactive inbox/dashboard/presence traffic for the same pool.

## Safety Rules

- [ ] Do not bundle this work with unrelated feature changes.
- [ ] Keep every phase deployable and reversible on its own.
- [ ] Use feature flags for behavioral changes in request/session lifecycle.
- [ ] Do not increase pool size as the primary fix.
- [ ] Do not remove fallback behavior until production metrics confirm stability.

## Success Criteria

- [ ] `idle in transaction` is effectively zero during normal traffic.
- [ ] App DB session usage stays comfortably below the pool ceiling during peak interactive usage.
- [ ] Inbox, dashboard, presence, and widget endpoints no longer slow down together in bursts.
- [ ] Heavy admin report pages no longer degrade interactive request latency.
- [ ] The rollout can be reversed quickly at any phase without data loss.

## Phase 1: Observability

- [x] Add metrics for request-scoped DB session usage.
- [x] Add metrics for transaction duration by request path.
- [x] Add metrics for pool checkout wait time if available from the engine/pool hooks.
- [x] Add metrics for live DB session state snapshots:
  - active
  - idle
  - idle in transaction
  - oldest transaction age
- [x] Add structured logs around auth/session validation queries.
- [x] Add structured logs around branding/settings reads.
- [x] Add structured logs around inbox list, inbox summary, presence, and dashboard live stats endpoints.
- [x] Add a low-noise production verification checklist for this phase.

### Phase 1 Exit Gate

- [x] We can identify, from app metrics and logs alone, which request families consume the most DB session time.
- [x] We can identify whether `idle in transaction` is rising during normal interactive usage or only during heavy-report bursts.

## Phase 2: Production Guardrails

- [x] Add DB-side `idle_in_transaction_session_timeout` with a conservative production-safe value.
- [x] Evaluate and add statement timeout guards for the heaviest synchronous report routes.
- [ ] Add safe degraded behavior for worst-case report requests if they exceed acceptable runtime.
- [x] Add route-level protection for the heaviest pages if needed:
  - rate limiting
  - serialized generation
  - cache-only mode under load

### Phase 2 Rollout Values

- [x] Start with `DB_IDLE_IN_TRANSACTION_SESSION_TIMEOUT_MS=15000`
- [x] Start with `DB_STATEMENT_TIMEOUT_MS=45000`
- [ ] Keep `BILLING_RISK_ROUTE_USE_CACHE=0` on the first deploy that introduces the code.
- [x] Enable `BILLING_RISK_ROUTE_USE_CACHE=1` only after confirming the billing-risk cache task has recent snapshots.
- [ ] Keep `CUSTOMER_RETENTION_ROUTE_USE_CACHE=0` until billing-risk cached mode is stable.
- [x] Enable `CUSTOMER_RETENTION_ROUTE_USE_CACHE=1` only after validating tracker and detail pages against live users.

### Phase 2 Implementation Notes

- Code support now exists for:
  - `DB_STATEMENT_TIMEOUT_MS`
  - `DB_IDLE_IN_TRANSACTION_SESSION_TIMEOUT_MS`
  - `BILLING_RISK_ROUTE_USE_CACHE`
  - `CUSTOMER_RETENTION_ROUTE_USE_CACHE`
- All four settings are disabled by default, so deploying the code alone does not change production behavior.

### Phase 2 Exit Gate

- [x] Broken or stuck requests can no longer tie up a DB session indefinitely.
- [x] A single heavy report cannot degrade the entire app as severely as it does today.

## Phase 3: Heavy Report Isolation

- [x] Identify the first report candidates to move off live synchronous request flow:
  - `/admin/reports/subscribers/billing-risk`
  - `/admin/customer-retention`
  - any other report path confirmed by Phase 1 metrics
- [x] Introduce background snapshot generation via Celery for the first report.
- [x] Render the last completed snapshot in the web request.
- [x] Add freshness metadata to the UI response context.
- [x] Keep the old synchronous path behind a fallback flag during rollout.
- [ ] Repeat for the next highest-impact report paths.

### Phase 3 Implementation Notes

- Verified live billing-risk snapshot cache exists with recent rows from Celery refresh.
- Verified Celery beat is scheduling `app.tasks.subscribers.refresh_billing_risk_cache`.
- Billing-risk and customer-retention routes now support cache-backed rendering behind:
  - `BILLING_RISK_ROUTE_USE_CACHE`
  - `CUSTOMER_RETENTION_ROUTE_USE_CACHE`
- Freshness metadata is attached to the route context so rollout verification can confirm snapshot age.

### Phase 3 Exit Gate

- [x] Heavy reports no longer compete directly with inbox/dashboard/presence traffic for request-time DB pool occupancy.

## Phase 4: Shared Request DB Session

- [x] Introduce a feature flag for shared request DB session behavior.
- [x] Ensure middleware-created request session can be reused by dependencies and auth helpers.
- [x] Update web auth dependencies to reuse request session instead of opening another session.
- [x] Update middleware readers such as audit and branding to reuse the same request session consistently.
- [x] Roll out on a narrow route set first.
- [ ] Expand gradually once metrics stay stable.

### Phase 4 Rollout Values

- [x] Deploy with `REQUEST_SHARED_DB_SESSION_ENABLED=0` first.
- [x] Start with `REQUEST_SHARED_DB_SESSION_ENABLED=1`.
- [x] Start with `REQUEST_SHARED_DB_SESSION_PATH_PREFIXES=/admin/dashboard,/admin/crm,/admin/reports,/admin/customer-retention`.
- [x] Confirm request-level DB session count drops on those routes before expanding prefixes.

### Phase 4 Implementation Notes

- Central DB dependency now supports request-session reuse via middleware session when enabled.
- Shared-session reuse is disabled by default.
- Route-prefix scoping is available via `REQUEST_SHARED_DB_SESSION_PATH_PREFIXES` for low-risk rollout.
- Initial hot-path coverage now includes admin auth, dashboard, CRM inbox list/detail/message, CRM presence, billing-risk, and customer-retention routes.

### Phase 4 Exit Gate

- [x] A normal request uses one DB session, not multiple independent sessions.
- [x] App session count drops under comparable traffic.

## Phase 5: Early Transaction Closure On Read Paths

- [x] Identify GET-heavy paths where reads finish well before response rendering completes.
- [x] Ensure read-only transaction scope ends as early as possible on those paths.
- [ ] Verify no hot path is left `idle in transaction` after the DB work is complete.
- [ ] Confirm auth/session, settings, inbox summary, presence, and dashboard reads no longer linger.

### Phase 5 Implementation Notes

- Added explicit read-only transaction release after admin auth/session validation.
- Added explicit read-only transaction release after audit-settings reads.
- Added explicit read-only transaction release after branding/settings reads.
- This reduces how long shared request sessions remain in a transaction before the route handler begins its own work.

### Phase 5 Exit Gate

- [ ] `idle in transaction` from normal interactive traffic is effectively eliminated.

## Phase 6: Hot-Path Request Cost Reduction

- [x] Cache or memoize per-request auth/permission lookups where safe.
- [x] Cache or memoize branding/settings reads where safe.
- [x] Avoid duplicate workqueue computation inside the same request.
- [x] Review inbox summary-counts request cost and reduce repeated count query load.
- [x] Move inbox snooze reopening or similar maintenance work out of request-time inbox list loading if confirmed by metrics.

### Phase 6 Implementation Notes

- The agent workqueue page no longer rebuilds the workqueue a second time just to populate the sidebar badge.
- `get_sidebar_stats()` now accepts a `workqueue_attention_override` so callers that already have the count can avoid recomputation.
- Inbox snooze reopening is now rate-limited per process during inbox list loads via `INBOX_SNOOZE_REOPEN_MIN_INTERVAL_SECONDS`.
- Default inbox snooze reopen cadence is now once every 15 seconds per process instead of once on every inbox list request.
- Inbox read helper results now memoize on `db.info` for the lifetime of the request/session:
  - assignment counts
  - inbox stats
  - resolved-today count
  - channel stats
- Inbox partial context now reuses already-loaded company time preferences instead of resolving them twice per render.
- Inbox assignment summary counts now fetch the simple buckets (`all`, `unassigned`, `unreplied`, `needs_attention`) in one DB round-trip instead of separate count queries.
- Inbox snooze reopening now runs from scheduled background maintenance via `app.tasks.crm_inbox.reopen_due_snoozed_conversations`.
- Inbox list loads no longer reopen due snoozes inline during request handling.

### Phase 6 Exit Gate

- [x] High-frequency endpoints have stable low DB cost under concurrent use.

## Phase 7: Pool Retuning

- [ ] Re-measure live session demand after Phases 1 to 6.
- [ ] Adjust `DB_POOL_SIZE` only after lifecycle and transaction issues are fixed.
- [ ] Adjust `DB_MAX_OVERFLOW` only if justified by measured burst behavior.
- [ ] Re-verify latency, pool occupancy, and session-state stability after retuning.

### Phase 7 Exit Gate

- [ ] Pool settings reflect measured steady-state demand instead of compensating for lifecycle bugs.

## Rollback Plan

- [ ] Every behavioral change must have a rollback path before deployment.
- [ ] Shared request session behavior must remain behind a feature flag until proven stable.
- [ ] Snapshot-backed reports must keep a fallback path during migration.
- [ ] If any phase regresses auth, routing, or template rendering, disable the new path before deeper debugging.

## Production Rollout Sequence

### Deploy 1: Code Only

- [x] Deploy current code with all new behavior flags still off.
- [x] Restart `app`, `celery-worker`, and `celery-beat`.
- [x] Verify the new scheduled task `crm_inbox_reopen_due_snoozed` appears in beat logs.
- [x] Verify `/health` is green and admin login still works.

Use:

```bash
docker compose restart app celery-worker celery-beat
docker compose logs -n 100 celery-beat
docker compose logs -n 100 app
```

### Deploy 2: Guardrails + Billing-Risk Cache

- [x] Set `DB_IDLE_IN_TRANSACTION_SESSION_TIMEOUT_MS=15000`
- [x] Set `DB_STATEMENT_TIMEOUT_MS=45000`
- [x] Set `BILLING_RISK_ROUTE_USE_CACHE=1`
- [x] Keep `CUSTOMER_RETENTION_ROUTE_USE_CACHE=0`
- [x] Restart `app` and `celery-worker`
- [x] Verify billing-risk pages render from cached snapshots
- [x] Verify no spike in 500/502/503/504 or timeout errors

Recommended env block:

```env
DB_IDLE_IN_TRANSACTION_SESSION_TIMEOUT_MS=15000
DB_STATEMENT_TIMEOUT_MS=45000
BILLING_RISK_ROUTE_USE_CACHE=1
CUSTOMER_RETENTION_ROUTE_USE_CACHE=0
REQUEST_SHARED_DB_SESSION_ENABLED=0
REQUEST_SHARED_DB_SESSION_PATH_PREFIXES=
```

### Deploy 3: Narrow Shared Request Session Rollout

- [x] Set `REQUEST_SHARED_DB_SESSION_ENABLED=1`
- [x] Set `REQUEST_SHARED_DB_SESSION_PATH_PREFIXES=/admin/dashboard,/admin/crm,/admin/reports,/admin/customer-retention`
- [x] Restart `app`
- [x] Verify admin auth, inbox, dashboard, billing-risk, and retention pages manually
- [x] Verify request-scoped DB session count drops on those routes
- [x] Verify `idle in transaction` trends down during interactive use

Recommended env block:

```env
REQUEST_SHARED_DB_SESSION_ENABLED=1
REQUEST_SHARED_DB_SESSION_PATH_PREFIXES=/admin/dashboard,/admin/crm,/admin/reports,/admin/customer-retention
```

### Deploy 4: Customer-Retention Cache

- [x] Set `CUSTOMER_RETENTION_ROUTE_USE_CACHE=1`
- [x] Restart `app`
- [x] Validate tracker, detail, and outreach pages with live users
- [x] Confirm report traffic no longer drags down inbox/dashboard/presence

## Verification Commands

### App Logs

```bash
docker compose logs --since=30m app
docker logs --since=30m dotmac_omni_app
```

Focus on:

- `web_auth_validate_session_slow`
- `branding_settings_load_slow`
- `audit_settings_load_slow`
- `crm_inbox_summary_counts_slow`
- `crm_inbox_conversations_partial_slow`
- `crm_inbox_conversation_detail_slow`
- `crm_presence_upsert_slow`
- `crm_presence_self_slow`
- `dashboard_live_stats_partial_slow`

### Metrics

If `METRICS_TOKEN` is configured:

```bash
curl -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Watch for:

- DB session open gauges
- DB transaction duration histograms
- DB pool checked-out / size / overflow gauges
- DB runtime sessions and `idle in transaction`

### Database State

```bash
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select
  count(*) as total,
  count(*) filter (where state = 'active') as active,
  count(*) filter (where state = 'idle') as idle,
  count(*) filter (where state = 'idle in transaction') as idle_in_tx,
  coalesce(max(extract(epoch from (now() - xact_start))), 0) as oldest_xact_age_seconds
from pg_stat_activity
where datname = current_database();
"
```

Success signal:

- `idle in transaction` trends toward zero during normal web usage
- oldest open transaction age remains low
- unrelated admin routes stop slowing down in the same burst window

## Immediate Rollback Order

- [ ] If auth or page rendering breaks after shared-session enablement:
  disable `REQUEST_SHARED_DB_SESSION_ENABLED` and restart `app`
- [ ] If report rendering regresses after cache rollout:
  disable `BILLING_RISK_ROUTE_USE_CACHE` or `CUSTOMER_RETENTION_ROUTE_USE_CACHE` and restart `app`
- [ ] If guardrails are too aggressive:
  set `DB_STATEMENT_TIMEOUT_MS=0` and/or `DB_IDLE_IN_TRANSACTION_SESSION_TIMEOUT_MS=0`, then restart `app`

## Execution Notes

- Current work status: code-side work through Phase 6 is complete; the next step is staged production rollout and live verification.
- This checklist should be updated by checking boxes only when code is merged and verified in the target environment.
