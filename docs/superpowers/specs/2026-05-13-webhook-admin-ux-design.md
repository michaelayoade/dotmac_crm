# Webhook Admin UX — Design Spec

**Date:** 2026-05-13
**Roadmap ref:** `docs/plans/master-implementation-roadmap.md` → Phase 5 / Channels & Integrations → "Webhook management UX" (item #31)
**Status:** Draft — awaiting user review

---

## 1. Summary

Consolidate webhook administration into the existing `/admin/integrations/webhooks` section and add the missing management surfaces: subscription editor, delivery log with filters and replay, test-fire, secret rotation, and a header health panel. The `/admin/system/webhooks*` duplicates are removed in favor of permanent (308) redirects.

The underlying model, service, API, signing, and Celery delivery layers are already complete — this work is admin-UX-only plus three small service helpers (`rotate_secret`, `send_test`, `replay`).

---

## 2. Goals & non-goals

### Goals
- Single canonical admin location for webhook configuration.
- Admins can subscribe/unsubscribe endpoints to any of the 40 event types from the UI.
- Admins can audit, filter, and **replay** past deliveries — including failed ones — without writing a SQL query or shell command.
- Admins can **rotate** a webhook signing secret without touching the database.
- Admins can **test-fire** a synthetic event to validate an endpoint end-to-end.
- A glanceable health panel surfaces success rate / failures / active count.

### Non-goals (v1)
- Per-endpoint payload filters or transforms.
- Bulk replay / batch operations on the delivery log.
- Endpoint-level rate limits.
- Inbound webhook configuration UI (e.g., Meta/WhatsApp inbound — separate concern).
- New permission scopes beyond the existing admin gate (the broader `require_permission` gap on integrations routes is flagged for the security pass, not this slice).
- Secret rotation grace period (old secret accepted for N minutes); deferred to v2 if a real customer needs it.
- Bulk endpoint deactivation / migration tooling.
- Endpoint health alerting changes (existing `webhook_health` Celery task is unchanged).

---

## 3. Audience & permissions

- Audience: admin users only. Same access gate as the rest of `/admin/integrations/*`.
- All state-changing routes use PRG with `status_code=303` and emit an HX-Trigger toast where the UI is HTMX-driven.
- All admin POST forms include CSRF via `components/forms/csrf_input.html`.
- Each state-changing action logs an `AuditEvent` (see §6).

---

## 4. Information architecture

Canonical routes — all under `/admin/integrations/webhooks`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/integrations/webhooks` | List + health panel |
| GET | `/admin/integrations/webhooks/new` | New endpoint form (existing) |
| POST | `/admin/integrations/webhooks` | Create endpoint (existing) |
| GET | `/admin/integrations/webhooks/{id}` | Detail (existing, expanded) |
| GET | `/admin/integrations/webhooks/{id}/edit` | Edit form (new) |
| POST | `/admin/integrations/webhooks/{id}` | Update endpoint (new) |
| POST | `/admin/integrations/webhooks/{id}/rotate-secret` | Regenerate signing secret (new) |
| POST | `/admin/integrations/webhooks/{id}/test` | Send synthetic delivery (new) |
| POST | `/admin/integrations/webhooks/{id}/subscriptions` | Add subscription (new) |
| POST | `/admin/integrations/webhooks/{id}/subscriptions/{sub_id}/delete` | Remove subscription (new) |
| GET | `/admin/integrations/webhooks/{id}/deliveries` | Delivery log with filters (new) |
| POST | `/admin/integrations/webhooks/{id}/deliveries/{delivery_id}/replay` | Replay a delivery (new) |

Removed:

| Old path | Replacement |
|---|---|
| `GET /admin/system/webhooks` | `308 → /admin/integrations/webhooks` |
| `GET /admin/system/webhooks/new` | `308 → /admin/integrations/webhooks/new` |
| `POST /admin/system/webhooks` | `308 → /admin/integrations/webhooks` (POST preserved by 308) |
| `GET /admin/system/webhooks/{id}/edit` | `308 → /admin/integrations/webhooks/{id}/edit` |
| `POST /admin/system/webhooks/{id}` | `308 → /admin/integrations/webhooks/{id}` |

The `Webhooks` sidebar entry under "System" is removed; the entry under "Integrations" remains.

---

## 5. UI components

### 5.1 List page

Layout from existing `templates/admin/integrations/webhooks/index.html`, with a new header strip:

- **Health panel** (three stat cards): Active endpoints · 24h success rate · 24h failures.
- Each endpoint row gets two new columns: **Last delivery** (relative time) and **Status** badge derived from recent activity:
  - `inactive` (is_active false) → slate
  - `failing` (≥1 failed delivery in last hour and 0 succeeded) → rose
  - `degraded` (any failed in last 24h, but also succeeded) → amber
  - `healthy` (succeeded ≥1 in 24h, no recent failures) → emerald
  - `idle` (no deliveries in 24h) → indigo

### 5.2 Detail page

Existing detail layout. Adds an action row:

`Edit` · `Send test event` · `Rotate secret` · `Deactivate` · `View delivery log`

Subscription editor replaces the read-only tag list:

- Render current subscriptions as removable chips (each chip is a tiny POST-delete form).
- "Add subscription" button opens a modal: full event-type picker grouped by domain header (subscriber / subscription / invoice / payment / usage / provisioning / network / support / custom). Each event has a one-line human description from a new `WEBHOOK_EVENT_DESCRIPTIONS` lookup.
- Adding a subscription is a single POST; the page redirects back with a toast.

A "Recent deliveries" mini-table (last 10) remains. Each row links to the full delivery log filtered to that delivery's status.

### 5.3 Edit page

New `templates/admin/integrations/webhooks/edit.html` mirroring the connector edit form pattern:

Name · URL · Connector (optional, dropdown of `ConnectorConfig`) · Active toggle · Cancel / Save.

The signing secret is **not** editable here — rotation is its own dedicated action so the value is never re-typed.

### 5.4 Rotate secret

Modal-style flow:

1. Confirm dialog ("Generate a new signing secret? The old secret will stop working immediately.").
2. On submit, the new secret is displayed once with a copy button and a warning banner that it won't be shown again.
3. Audit event written.

The old secret is overwritten in place. No grace period in v1.

### 5.5 Send test event

Button on detail page → POST → enqueues a `deliver_webhook` Celery task with:

```json
{
  "event_type": "custom",
  "test": true,
  "fired_at": "<iso8601 UTC>",
  "fired_by_person_id": "<actor uuid>",
  "endpoint_id": "<uuid>"
}
```

Result becomes a normal row in `webhook_deliveries` with `event_type="custom"`. Toast confirms enqueuing. Admin watches it in the delivery log.

### 5.6 Delivery log

`/admin/integrations/webhooks/{id}/deliveries`

- Paginated table (50 rows/page): created_at · event · status badge · attempts · response status · last error (truncated to 80 chars).
- Filters in a `filter_bar`: status (all/pending/delivered/failed) · event_type (dropdown of distinct events for this endpoint) · date range (last 24h / 7d / 30d / custom).
- Row click → modal showing full request payload (JSON-pretty), response status, headers, error.
- Per-row **Replay** button when status is `failed` or `delivered`. POST creates a new pending `WebhookDelivery` (does not mutate the original) and enqueues `deliver_webhook`.

---

## 6. Service-layer additions

In `app/services/webhook.py`:

```python
class WebhookEndpoints(ListResponseMixin):
    @staticmethod
    def rotate_secret(db: Session, endpoint_id: str) -> str:
        """Regenerate the signing secret and return the new value (only returned here)."""

    @staticmethod
    def send_test(db: Session, endpoint_id: str, *, actor_person_id: str | None) -> WebhookDelivery:
        """Create a pending WebhookDelivery with a synthetic test payload, enqueue deliver_webhook, return the row."""

    @staticmethod
    def list_with_stats(db: Session, *, limit: int, offset: int) -> list[EndpointStats]:
        """List endpoints joined with last_24h_delivered, last_24h_failed, last_delivery_at, pending_count."""


class WebhookDeliveries(ListResponseMixin):
    @staticmethod
    def replay(db: Session, delivery_id: str, *, actor_person_id: str | None) -> WebhookDelivery:
        """Clone a prior delivery as a new pending row and enqueue deliver_webhook. Original untouched."""

    @staticmethod
    def list_filtered(
        db: Session,
        endpoint_id: str,
        *,
        status: str | None,
        event_type: str | None,
        since: datetime | None,
        until: datetime | None,
        limit: int,
        offset: int,
    ) -> list[WebhookDelivery]: ...
```

`EndpointStats` is a `dataclass(frozen=True)` adjacent to the manager — not a new ORM model.

Test-fire and replay both end with `celery_app.send_task("app.tasks.webhooks.deliver_webhook", args=[str(delivery.id)])` — same path normal deliveries take. The existing retry/backoff and HMAC code is reused unmodified.

---

## 7. Audit events

Each state-changing admin route logs an `AuditEvent` via `log_audit_event`:

| Action | Entity | Metadata fields |
|---|---|---|
| `webhook_endpoint_created` | webhook_endpoint | name, url, is_active, subscription_count |
| `webhook_endpoint_updated` | webhook_endpoint | changed_keys (list), is_active |
| `webhook_endpoint_deactivated` | webhook_endpoint | (none) |
| `webhook_endpoint_secret_rotated` | webhook_endpoint | (none — never log the secret) |
| `webhook_endpoint_test_fired` | webhook_endpoint | delivery_id |
| `webhook_subscription_added` | webhook_subscription | endpoint_id, event_type |
| `webhook_subscription_removed` | webhook_subscription | endpoint_id, event_type |
| `webhook_delivery_replayed` | webhook_delivery | endpoint_id, source_delivery_id, event_type |

Actor is the current admin user. Status code 200. All writes occur after the underlying state change has been committed, inside the same request.

---

## 8. Data flow

### Create endpoint (existing, unchanged)

```
form POST → integrations.py → WebhookEndpoints.create → DB
  → for each event in form → WebhookSubscriptions.create
  → redirect 303 to detail
```

### Test-fire

```
button POST → integrations.py
  → WebhookEndpoints.send_test
      → build payload dict
      → WebhookDeliveries.create(status=pending, attempt_count=0)
      → celery_app.send_task("app.tasks.webhooks.deliver_webhook", args=[delivery_id])
  → log_audit_event("webhook_endpoint_test_fired")
  → redirect 303 to /deliveries with HX-Trigger toast
```

### Replay

```
button POST → integrations.py
  → WebhookDeliveries.replay
      → load source delivery
      → create new WebhookDelivery(status=pending, attempt_count=0, payload=source.payload,
                                    event_type=source.event_type, subscription_id=source.subscription_id,
                                    endpoint_id=source.endpoint_id)
      → celery_app.send_task("app.tasks.webhooks.deliver_webhook", args=[new.id])
  → log_audit_event("webhook_delivery_replayed")
  → redirect 303 back to /deliveries with HX-Trigger toast
```

### Rotate secret

```
button POST → integrations.py
  → WebhookEndpoints.rotate_secret → secrets.token_urlsafe(32) → set + commit → return value
  → log_audit_event("webhook_endpoint_secret_rotated")  # metadata excludes secret
  → render rotate_success.html with the new secret displayed once
```

---

## 9. Error handling

- All POST routes wrap the service call in try/except. On `HTTPException` the page re-renders with the error block at the top of the form; on unexpected exception, log + re-render with a generic "Action failed — see server logs" message.
- Test-fire and replay catch Celery enqueue failures and surface them as a toast. The pending `WebhookDelivery` row remains so it can be retried by the `retry_failed_deliveries` scheduled task.
- Delivery payload modal: malformed JSON in `payload` is shown as raw text rather than failing the page.
- Filter inputs are validated against the existing enum + `coerce_uuid` helpers; invalid filters return the unfiltered list, not an error page.

---

## 10. Testing

### Service tests (`tests/test_webhook_admin_services.py` — new file)
- `rotate_secret` changes the value, returns the new secret, is auditable.
- `send_test` creates a pending delivery with `event_type=custom` and `payload.test == True`.
- `replay` creates a new pending row identical to the source (except id, status, timestamps), original is untouched.
- `list_with_stats` returns correct 24h delivered / 24h failed / last_delivery_at counts using a seeded fixture.
- `list_filtered` honors status + event_type + date range filters.

### Route tests (`tests/test_webhook_admin_web.py` — new file)
- GET list page renders health panel.
- GET edit returns 200 + prefilled form.
- POST update applies changes and redirects.
- POST rotate-secret responds with success page; secret value present in body once.
- POST test enqueues a delivery (mock `celery_app.send_task`); audit row exists.
- POST subscriptions adds + removes.
- GET deliveries with each filter combination.
- POST replay creates a new row, preserves the source row.
- Removed system routes return `308 Permanent Redirect` to the integrations equivalents (route, method, and trailing path preserved).

### Playwright E2E (`tests/playwright/e2e/test_webhook_admin.py` — new file)
- Happy path: create endpoint → add two subscriptions → fire test event → see new delivery row → replay it → see second delivery row.

### What we do NOT need to retest
- HMAC signing — covered indirectly by the existing delivery task; unchanged.
- Retry/backoff — unchanged.

---

## 11. Migration / rollout

- No DB migration required for v1.
- One template+route consolidation: the `/admin/system/webhooks*` views are removed; sidebar entry under "System" is deleted.
- Feature is on by default (no flag). The new admin actions are additive; existing data flows are untouched.
- Search-and-destroy step: grep for `/admin/system/webhooks` across the repo to ensure no internal link still points to the old URLs.

---

## 12. Implementation order (for the plan)

1. Service helpers (`rotate_secret`, `send_test`, `replay`, `list_with_stats`, `list_filtered`) + their unit tests — TDD.
2. New routes in `integrations.py` + audit calls + route tests.
3. New templates (edit, deliveries, rotate_success, subscription picker partial) + sidebar update.
4. Stats badges + health panel.
5. System-route redirects + sidebar cleanup.
6. Playwright happy-path.
7. Verification pass (`pytest`, `ruff`, `mypy`).

---

## 13. Open question (deferred)

The admin integrations routes today lack `Depends(require_permission(...))`. CLAUDE.md mandates it. We intentionally do **not** add new gates in this slice — it stays consistent with the surrounding file. The webhook admin pages should be included in the future blanket pass that adds permission gates across `app/web/admin/integrations.py`.
