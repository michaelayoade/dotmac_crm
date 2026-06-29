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

## Outcomes

Execution results use `WorkOutcome`:

- `no_billing_change` for internal jobs, surveys, or repairs that do not affect billing.
- `activation_requested`, `subscriber_created`, and `subscriber_updated` for dotmac_sub handoffs.
- `repair_completed` and `disconnect_completed` for operational closure.

External handoffs must use an idempotency key when possible so retries do not duplicate outcomes.
