# Work Lifecycle Spine

DotMac CRM treats tickets, projects, project tasks, work orders, and subscribers as first-class entities in one operational flow:

`demand -> orchestration -> planning -> execution -> outcome`

## Entity Roles

| Stage | Entity | Role |
| --- | --- | --- |
| Demand | Ticket or lead | Captures what the customer wants or what broke. |
| Orchestration | Project | Coordinates a multi-step delivery effort. |
| Planning | ProjectTask | Defines discrete planned steps. |
| Execution | WorkOrder | Schedules and tracks real-world or field execution. |
| Outcome | Subscriber / dotmac_sub | Records what became active, changed, or billable. |

## WorkOrder Contract

`WorkOrder` is the shared execution primitive. It does not mean "billable work."

Use a WorkOrder for installs, repairs, surveys, disconnects, maintenance, and internal field jobs. Billing or subscriber changes are represented as explicit outcomes, not assumed from the presence of a WorkOrder.

## Cross-Stage Links

Cross-stage relationships use `WorkLink`:

- `source_type` / `source_id`: what demanded or caused the next stage.
- `target_type` / `target_id`: what was created or satisfied.
- `link_type`: `originated`, `fulfills`, `blocks`, `related`, or `resulted_in`.
- `contract_name`: the named automation or handoff that created the link.

Existing direct foreign keys such as `work_orders.ticket_id` remain compatibility fields while reads and automations move toward `WorkLink`.

Work-order ticket/project filters read both legacy direct foreign keys and `WorkLink` origin records during the migration period.

## Outcomes

Execution results use `WorkOutcome`:

- `no_billing_change` for internal jobs, surveys, or repairs that do not affect billing.
- `activation_requested`, `subscriber_created`, and `subscriber_updated` for dotmac_sub handoffs.
- `repair_completed` and `disconnect_completed` for operational closure.

External handoffs must use an idempotency key when possible so retries do not duplicate outcomes.

Work-order completion records one idempotent `WorkOutcome` using `work-order:{id}:completion`. Internal/non-billing work records `no_billing_change`; subscriber-backed installs, repairs, and disconnects record the corresponding operational outcome and carry the dotmac_sub external reference when available.

A failed dotmac_sub push leaves the outcome `pending`. The `app.tasks.field.reconcile_pending_work_outcomes` sweep (scheduler domain, default-on, ~30 min) re-drives pending selfcare outcomes and flips recovered ones to `succeeded`, so a transient sub outage self-heals.

## Named Contracts

Cross-stage handoffs are explicit, named, and opt-in — never implicit. The originating ticket of a completed work order is resolved only when `workflow.work_order_completion_resolves_ticket` is enabled (default off), so a WorkOrder closing its ticket is a deliberate decision rather than a silent side effect. When it fires, the closure is itself recorded as a `WorkLink` (`work_order` `resulted_in` `ticket`, contract `work_order.completed.resolved_ticket`).

## Link Audits

Because `WorkLink` is polymorphic, `source_id` and `target_id` cannot have ordinary database foreign keys. Use `work_lifecycle.dangling_links()` for periodic checks that identify missing or inactive source/target records.
