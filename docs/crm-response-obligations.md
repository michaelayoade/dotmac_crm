# CRM Response Obligations

## Ownership

`dotmac_crm` is the system of record for whether a customer conversation is
awaiting a response. The named owner is
`app.services.crm.inbox.response_obligations` and its materialized decision is
the one-to-one `crm_response_obligations` row.

Message ingestion, outbound delivery, conversation lifecycle, priority, and
assignment services write facts. They do not independently decide whether a
response is due. Existing write paths enter the owner through the conversation
projection gateway; the bounded reconciler repairs drift if a caller or worker
misses that update.

## Decision contract

The owner derives exactly one current state:

| State | Meaning |
|---|---|
| `no_customer_message` | No inbound customer message exists. |
| `awaiting_first_response` | An inbound message exists and no meaningful successful outbound response follows it. |
| `awaiting_follow_up` | The customer sent a newer inbound message after a prior response. |
| `responded` | A meaningful successful outbound response is at least as recent as the latest inbound message. |
| `snoozed` | The conversation is intentionally deferred. |
| `resolved` | The conversation is inactive, resolved, or resolved to a ticket. |

Failed outbound delivery does not discharge an obligation. AI intake and other
automated messages marked `ai_intake_generated` or
`response_obligation_exempt` do not discharge it. Historical outbound messages
without an author count unless explicitly marked exempt, preserving imported
conversation behavior.

For an awaiting state, the row owns:

- the triggering inbound message and latest inbound/outbound timestamps;
- the priority-derived `response_due_at`;
- the accountable agent, team, or explicit `unassigned` scope;
- breach and escalation state, including the indexed `next_escalation_at`.

The initial owner reminder uses the configured reminder delay (five minutes by
default); it does not wait for the response SLA to breach. Team-lead and
operations escalation intervals are independently configurable. `breached_at`
is set only once the priority-derived response SLA is actually overdue.

## Consequences and projections

- Inbox `unreplied` and `needs_attention` flags are projections of the
  obligation state.
- The workqueue reads due time and pending inbound time from the obligation.
- The reminder/escalation worker queries only the indexed obligation table. It does not scan
  the message table to find overdue work.
- Notifications escalate from the assigned agent (or team queue), to the
  linked service-team lead/manager, then to active support/operations leads.
- The legacy response-SLA checker observes obligation breaches but does not
  send response alerts; this prevents two schedulers from deciding the same
  consequence. Resolution-SLA behavior remains separate.

## Reconciliation and failure behavior

`reconcile_response_obligations` processes the stalest or missing rows in
bounded batches. It is idempotent and rotates through active conversations via
`reconciled_at`. Before a due notification is produced, source facts are
reconciled again under the worker transaction.

If no real escalation recipient can be resolved, the worker records a system
recipient and increments `missing_recipients`; this makes the ownership
configuration failure observable instead of silently dropping the breach.

## Authority migration

- **Old decision paths:** `Conversation.first_response_at`, message-history
  scans in reminder code, metadata SLA fields in the workqueue, and separately
  derived inbox summary flags.
- **New owner:** `ResponseObligation` plus the response-obligation policy
  service.
- **Backfill:** the migration materializes current decisions set-wise on
  PostgreSQL; the reconciler repairs and refreshes them using effective
  settings.
- **Verification/cutover gate:** migration head validation; focused transition,
  escalation, inbox, SLA, workqueue, and task tests; zero missing obligation
  rows after reconciliation; due-worker `missing_recipients` monitored.
- **Cutover:** inbox summaries and workqueue now project/read the new owner;
  response alerts come only from its due worker.
- **Fallback:** set `crm_inbox_response_obligations_enabled=false` to disable
  both scheduled consequence/reconciliation tasks while retaining the table
  and projections for inspection. Do not restore metadata or
  `first_response_at` as a parallel decision owner.
- **Retirement:** `first_response_at` remains a historical performance metric,
  not current-response state. The old heavy reminder scanner is no longer on
  the scheduled path and can be deleted after production verification.
