# Route Workload Reduction Plan

## Objective

Reduce the remaining request-time latency after the DB session rollout by removing synchronous route work that still happens during page loads.

This plan is for the **current** slowdown class:

- not the earlier app-wide DB pool collapse
- not generic “maybe this query is slow”
- specifically the route handlers and helpers still doing too much work per request

## Verified Current State

The earlier session-lifecycle fix improved the app, but several routes are still slow because they still do expensive synchronous work during request handling.

Recent live checks showed:

- DB was **not** in the earlier collapse pattern during the later checks
  - sample observed: `idle in transaction=1`, `active=1`, `app sessions=38`, `total=38`
- the remaining slow routes are now dominated by request-time workload
- the worst remaining families are:
  - CRM inbox list/detail/counts
  - CRM presence routes affected by inbox traffic
  - billing-risk
  - customer-retention
  - dashboard
  - some support/ticket pages

## Route Families

### Family 1: CRM Inbox

Routes:

- `/admin/crm/inbox/summary-counts`
- `/admin/crm/inbox/conversations`
- `/admin/crm/inbox/conversation/{conversation_id}`
- `/admin/crm/inbox/conversation/{conversation_id}/message`
- `/admin/crm/inbox/attachment/{message_id}/{attachment_index}`

Verified current behavior:

- `summary-counts` still hits synchronous query helpers on cache miss
- `conversations` still builds and enriches up to `limit=150` rows synchronously
- `conversation/{conversation_id}` still assembles full thread context synchronously
- attachment and detail requests still cluster during inbox usage bursts

Exact code paths:

- [app/web/admin/crm_inbox_conversations.py](/root/dotmac/dotmac_omni/app/web/admin/crm_inbox_conversations.py)
- [app/services/crm/inbox/page_context.py](/root/dotmac/dotmac_omni/app/services/crm/inbox/page_context.py)
- [app/services/crm/inbox/listing.py](/root/dotmac/dotmac_omni/app/services/crm/inbox/listing.py)
- [app/services/crm/inbox/queries.py](/root/dotmac/dotmac_omni/app/services/crm/inbox/queries.py)

Definitive remaining causes:

- large synchronous page assembly
- repeated count/aggregate queries
- `limit=150` list rendering
- thread context assembly with templates/macros/activity/context lookups
- concurrent browser requests to the same inbox surfaces

### Family 2: CRM Presence

Routes:

- `/admin/crm/agents/presence`
- `/admin/crm/agents/presence/self`

Verified current behavior:

- these routes still slow down during inbox bursts
- `presence/self` is not a trivial row fetch; it computes shift/time/status state

Exact code paths:

- [app/web/admin/crm_presence.py](/root/dotmac/dotmac_omni/app/web/admin/crm_presence.py)

Definitive remaining causes:

- synchronous auth + agent lookup
- synchronous presence write or presence-state read
- for `presence/self`, additional shift-window and `seconds_by_status(...)` work
- same users/pages often trigger these requests repeatedly while inbox is active

### Family 3: Billing Risk

Routes:

- `/admin/reports/subscribers/billing-risk`
- `/admin/reports/subscribers/billing-risk/rows`
- `/admin/reports/subscribers/billing-risk/export`

Verified current behavior:

- live request observed at about `9.6s`
- route is in cache mode, but still slow

Exact code paths:

- [app/web/admin/billing_risk.py](/root/dotmac/dotmac_omni/app/web/admin/billing_risk.py)
- [app/services/billing_risk_cache.py](/root/dotmac/dotmac_omni/app/services/billing_risk_cache.py)

Definitive remaining causes:

- main page still calls `_billing_risk_rows_source(...)` twice
  - once with `limit=51`
  - once with `limit=10000`
- page still computes request-time metrics from the large result set
- page still fetches overdue invoices live
- page still builds full page context synchronously

This route is no longer slow because of the old live builder path by default. It is still slow because the cached route still performs too much synchronous processing.

### Family 4: Customer Retention

Routes:

- `/admin/customer-retention`
- `/admin/customer-retention/{customer_id}`
- `/admin/customer-retention/engagements`
- `/admin/customer-retention/outreach`

Verified current behavior:

- tracker and related routes still load large datasets in request-time code
- engagement route can be slow when called with a large `customer_id` list

Exact code paths:

- [app/web/admin/billing_risk.py](/root/dotmac/dotmac_omni/app/web/admin/billing_risk.py)

Definitive remaining causes:

- `customer_retention_tracker(...)` still loads up to `6000` rows synchronously
- tracker still runs pipeline shaping and engagement/history joins in Python
- detail and outreach paths still reload the large retention row set
- `/engagements` groups all matching engagement rows for the requested IDs in the request

### Family 5: Dashboard

Routes:

- `/admin/dashboard`
- `/admin/dashboard/live-stats`

Verified current behavior:

- dashboard routes are improved, but still show multi-second requests in live traffic

Exact code paths:

- [app/services/web_admin_dashboard.py](/root/dotmac/dotmac_omni/app/services/web_admin_dashboard.py)
- [app/services/web_admin.py](/root/dotmac/dotmac_omni/app/services/web_admin.py)

Definitive remaining causes:

- full dashboard still composes several expensive sections synchronously
- live stats still run multiple aggregate queries over tickets, leads, inbox, and queue data
- page shell still builds admin context plus route context in the same request

### Family 6: Support / Tickets

Routes:

- `/admin/support/tickets/{ticket_ref}`
- `/admin/support/tickets`
- related lookup and create paths

Verified current behavior:

- support/ticket pages appear in the live logs with slower requests
- they are not the dominant family, but they are still part of the route-wide issue

Exact code paths:

- support ticket handlers under `app/web/admin/`
- shared admin context/helpers

Definitive remaining causes:

- synchronous page context on admin shell routes
- some ticket flows still carry shared admin/report context cost
- ticket detail and related screens can still ride the same request-time aggregate work as other admin pages

## Fix Strategy

This should not be tackled as unrelated one-off route fixes.

The right model is:

1. fix shared patterns
2. apply those patterns to route families in order of impact

### Shared Patterns To Apply

- move expensive derivations off request path
- precompute or cache large report metrics
- lower request-time row volume
- split page shell from live widgets
- add short-TTL or coalesced cache for repeated admin partials
- stop loading full context when the user only needs a partial

## Workstreams

### Workstream A: Inbox Runtime Reduction

Goal:

- make inbox list/count/detail cheap enough that active operators no longer create 10s to 15s bursts

Planned actions:

- reduce inbox conversation page size from `150`
- add short-TTL coalesced cache for `/inbox/summary-counts`
- stop loading full conversation detail support data on initial thread open
- move nonessential detail enrichments to follow-up partials
- profile and trim `build_inbox_conversations_partial_context(...)`
- reduce duplicate browser hits where the same user/page requests counts/list/detail together

Success target:

- inbox list under normal load stays in low single-digit seconds or below
- summary counts remain sub-second in typical use
- detail pane stops spiking into burst-tier latency

### Workstream B: Report Snapshot Completion

Goal:

- make billing-risk and retention read prepared report artifacts rather than large cached row sets plus request-time metric computation

Planned actions:

- billing-risk:
  - precompute page metrics inside snapshot refresh
  - stop dual loading (`51` + `10000`) on page render
  - serve first page rows and metrics from snapshot artifacts
- customer-retention:
  - create dedicated retention snapshot view/artifact
  - stop loading `6000` base rows synchronously in page handlers
  - make tracker/detail/outreach consume prepared retention rows
- engagement endpoints:
  - paginate or chunk large `customer_id` fanout requests

Success target:

- billing-risk initial render under 1 to 2 seconds
- retention tracker no longer depends on loading thousands of rows in request time

### Workstream C: Shared Admin Context Reduction

Goal:

- stop every admin page from rebuilding expensive shared UI context synchronously

Planned actions:

- profile `build_admin_context(...)`
- split “page shell” context from “live widget” context
- cache short-TTL sidebar/admin stats
- keep notifications/sidebar fast and bounded

Success target:

- dashboard, notifications, ticket pages, and report pages stop inheriting unnecessary shared work

### Workstream D: Support / Ticket Path Cleanup

Goal:

- keep support pages responsive even during CRM/report activity

Planned actions:

- inspect ticket detail/context assembly
- isolate ticket page shell from unnecessary admin/report aggregates
- defer secondary widgets/related sections into partials

Success target:

- support/ticket routes stay in low single-digit seconds even during CRM activity

## Execution Order

1. Inbox runtime reduction
2. Billing-risk snapshot completion
3. Customer-retention snapshot completion
4. Shared admin context reduction
5. Support/ticket path cleanup
6. Dashboard final slimming
7. Re-evaluate pool tuning only after route workload drops

## What Not To Do

- do not raise pool size first
- do not treat cached source rows as sufficient if page-time metrics still scan thousands of rows
- do not redesign every route at once
- do not bundle this with unrelated feature work

## Verification Plan

For each workstream:

- collect live slow-route timings before the change
- deploy only that route-family change
- compare:
  - p95 or top observed durations
  - concurrent request burst behavior
  - DB session state during the same traffic pattern

## Immediate Next Step

Start with **Workstream A: Inbox Runtime Reduction** and **Workstream B: Billing-Risk Snapshot Completion**.

That is the highest-payoff combination because:

- inbox is still the worst interactive family
- billing-risk and retention are still expensive even after cache rollout
- both families still perform too much synchronous request-time processing
