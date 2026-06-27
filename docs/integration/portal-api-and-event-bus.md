# Customer Portal API + Unified Event Bus

**Status:** Draft / RFC
**Owners:** Platform (CRM) + Selfcare (sub)
**Scope:** Surface CRM customer-lifecycle features (referrals, projects, work orders, field service, quotes) inside the customer mobile app, and consolidate all CRM↔sub data exchange onto one contract.

---

## 1. Why

The customer app (`dotmac_sub/mobile`) should let customers track installations, follow technician visits, accept quotes, and refer friends — all of which live in `dotmac_crm`. Today CRM exposes **no customer-scoped API**; everything is admin/technician-only.

We already operate a working CRM↔sub integration, but as **several bespoke pipelines**:

| Flow | Direction | Today |
|---|---|---|
| Subscriber sync | sub→CRM | `crm_sync.push_subscriber_change` (+ `CrmSyncFailure` DLQ + `redrive_crm_dead_letters`) |
| Billing snapshot | sub→CRM | `crm_billing_push.push_crm_billing_snapshots` |
| Tickets | sub→CRM | `crm_ticket_push.push_ticket_to_crm` / `push_comment_to_crm` |
| Tickets | CRM→sub | `crm_ticket_pull.sync_ticket_by_id` |
| Chat replies | CRM→sub | `message_outbound` webhook → `push_service.send_push` |
| Chat session | sub→CRM | `widget_internal` mint → `visitor_token` |

This RFC **(a)** adds a customer-scoped **Portal API** the app talks to **directly** (brokered by sub, mirroring the chat widget), and **(b)** refactors every flow above onto **one unified event envelope + bus**.

## 2. Principles

1. **System of record per domain.** The app reads each domain from its owner — billing/usage/wallet from **sub**, projects/work-orders/referrals/tickets from **CRM**. Never read a domain back from its synced replica (CRM holds a sub billing snapshot for agent 360, *not* to serve billing to the app).
2. **Authorization where the data lives.** CRM enforces row-level scoping on every Portal request; sub never filters CRM data on CRM's behalf.
3. **One contract.** All async exchange uses a single signed, idempotent, versioned event envelope with a shared DLQ + redrive — in both directions.
4. **Reuse proven patterns.** The Portal token is the chat `visitor_token` generalized; the event bus is the `message_outbound` webhook generalized.

## 3. Architecture — four planes

```
            ┌─────────────────────────── mobile app ───────────────────────────┐
            │  sub domains (billing, usage, wallet)   CRM domains (portal)        │
            └───────┬───────────────────────────────────────────┬───────────────┘
                    │ Bearer (sub JWT)                            │ Bearer (portal_token)
              ┌─────▼─────┐   (1) mint portal_token         ┌─────▼─────────┐
              │   sub     │────────────────────────────────▶│  CRM Portal   │  (2) Portal API
              │  (BFF +   │◀─────────  events  ────────────▶│   + Events    │
              │  broker)  │      (3) unified event bus       └───────────────┘
              └───────────┘   (4) sync feeds CRM 360
```

- **(1) Identity/token** — sub brokers a short-lived, subscriber-scoped `portal_token`.
- **(2) Portal API** — CRM `/api/v1/portal/*`, row-scoped, customer-safe DTOs, reads + writes.
- **(3) Event bus** — one envelope, both directions, the unified contract (§6).
- **(4) Sync** — sub→CRM subscriber/billing/ticket feeds become *producers on the bus* (§6.4).

---

## 4. Identity & the Portal token (mirror chat)

Generalize `app/api/crm/widget_internal.py` (the chat mint) into a portal mint.

### 4.1 Mint (service-to-service)
```
POST /api/v1/portal/internal/session            [service account auth]
Authorization: Bearer <CRM service-account JWT>     # crm_client.py login
{
  "external_id": "<splynx id>",            # or "crm_subscriber_id"
  "email": "customer@example.com",
  "name": "Jane Doe",
  "scopes": ["read:projects","read:work_orders","read:referrals",
             "write:referrals","read:quotes","write:quotes","write:service_requests"]
}
→ 200 {
  "portal_token": "<opaque/JWT>",          # bound to crm_subscriber_id + scopes
  "crm_subscriber_id": "<uuid>",
  "expires_at": "2026-06-27T12:15:00Z",    # ~15 min, like visitor_token
  "api_base": "https://crm.dotmac.io/api/v1/portal"
}
```
- Allowed callers gated by `PORTAL_MINT_SERVICE_ACCOUNTS` (rename of `CHAT_MINT_SERVICE_ACCOUNTS`; chat uses the same list).
- `crm_subscriber_id` resolution reuses `crm_sync` linkage (kept fresh) → `crm_client.resolve_subscriber_id`.

### 4.2 App flow (identical to chat)
1. App → sub `POST /me/portal/session` (sub auth).
2. Sub mints via §4.1, returns `{portal_token, api_base, expires_at}` to the app.
3. App calls CRM `GET {api_base}/...` with `Authorization: Bearer <portal_token>`.
4. On `401`, app calls sub to re-mint and replays once — the same interceptor the app already has for sub's JWT.

### 4.3 Token
- Short-lived (15 min). No refresh token; re-mint via sub (sub holds the trust).
- Claims: `sub=crm_subscriber_id`, `actor=subscriber`, `scopes`, `exp`, `iss=crm`, `aud=portal`. Signed by CRM.
- CRM scopes **every** Portal query to `sub` claim. A missing/extra scope → 403.

### 4.4 Reseller identity & partner linkage

Today sub's `Reseller` (`app/models/subscriber.py`) has **no CRM link** — unlike `Subscriber` (`crm_subscriber_id` + `splynx_customer_id`). CRM already has the home for it: `Organization` with `AccountType.reseller` and **parent/child hierarchy** (`organization_membership` describes "a reseller managing many child customer orgs"). We link them and scope managed accounts via that hierarchy.

- **Link**: add `crm_organization_id` to sub `Reseller` (mirror `Subscriber.crm_subscriber_id`: unique, nullable, sync-backfilled).
- **Maintained by** a `reseller.changed` event (sub→CRM, §6.3) that upserts the reseller `Organization(account_type=reseller)` and persists `crm_organization_id` — the reseller analogue of `_persist_crm_link`.
- **Managed accounts = org subtree**: a subscriber's `reseller_id` (sub) places that subscriber's CRM record as a **child org** under the reseller's Organization (`parent_id`). "This reseller's accounts" is then a subtree query in CRM — **authorization stays server-side**; sub never passes an account list.
- **People**: sub `ResellerUser` ↔ CRM `Person` + `OrganizationMembership` (portal logins).

### 4.5 Reseller portal token (subtree-scoped)
- Same mint (§4.1), reseller variant: `{crm_organization_id | reseller_external_id, scopes:[…]}`.
- Claims: `sub=crm_organization_id`, **`actor=reseller`** — tells CRM to apply **subtree** scoping (the org + its child customer orgs) instead of single-subscriber scoping.
- Reseller scopes: `read:managed_accounts`, `read:projects`, `read:work_orders`, `read:referrals` (own/partner), plus writes (`write:service_requests`) as needed.
- The reseller portal calls the **same** `/api/v1/portal/*`; CRM returns the subtree, not one subscriber. Aligns with sub's existing reseller impersonation model.
- **SoR boundary holds**: reseller **commissions/billing are sub-owned** — the app reads those from sub, *not* the Portal API. Sync feeds CRM's partner view for agent context only.

---

## 5. Portal API (v1) — `/api/v1/portal`

All endpoints: `Bearer portal_token`, auto-scoped to the token's subject — a single subscriber (`actor=subscriber`) or a reseller org subtree (`actor=reseller`, §4.5) — **customer-safe DTOs only** (no internal notes, costs, SLA timers, tech rates, other customers). 404 (not 403) for non-owned ids.

### 5.1 Referrals (reference vertical — see §7)
```
GET  /portal/referrals
→ { "code":"AB12CD34", "share_url":"https://dotmac.io/r/AB12CD34",
    "program": {"reward_amount":5000,"currency":"NGN"},
    "referrals":[ {"name":"K. A.","status":"qualified",
                   "reward_amount":5000,"reward_status":"issued","created_at":"..."} ],
    "totals": {"referred":4,"qualified":2,"rewarded":1,"earned":5000} }

POST /portal/referrals          {"name":"...","email":"...","phone":"..."}
→ 201 { "referral_id":"<uuid>", "status":"pending" }     # wraps referral capture
```

### 5.2 Projects (installation tracker)
```
GET /portal/projects                  → [ {id,name,number,status,progress_pct,due_at,coordinator_name} ]
GET /portal/projects/{id}             → { ...summary, stages:[{title,status,completed_at}], next_milestone }
```

### 5.3 Work orders / field service
```
GET  /portal/work-orders              → [ {id,reference,work_type,status,scheduled_start,scheduled_end} ]
GET  /portal/work-orders/{id}         → { ...,estimated_arrival_at,technician:{name,photo_url},
                                          events:[{event,occurred_at}], attachments:[{kind,captured_at}] }
POST /portal/service-requests         {"work_type":"repair","description":"...","preferred_window":"..."}
→ 201 { "work_order_id":"<uuid>", "status":"draft" }   # customer-initiated visit
```
> Live technician GPS/ETA is **not** on the event bus. Stream it over the existing widget-style WebSocket (`/ws/portal?token=`), opt-in via `FieldTechPresence.location_sharing_enabled`. The bus carries only discrete `work_order.en_route|started|completed` transitions.

### 5.4 Quotes / sales
```
GET  /portal/quotes                   → [ {id,status,total,currency,expires_at} ]
GET  /portal/quotes/{id}              → { ...,line_items:[{description,quantity,unit_price,amount}] }
POST /portal/quotes/{id}/accept       → 200 { "status":"accepted","sales_order_id":"<uuid>" }
```

### 5.5 Reseller (subtree-scoped — `actor=reseller` token)
Same endpoints, but CRM scopes to the reseller org's subtree (§4.5). `?account=` must resolve inside the subtree or → 404.
```
GET /portal/managed-accounts          → [ {crm_subscriber_id,name,status} ]   # the org subtree
GET /portal/projects?account={id}     → that managed account's projects
GET /portal/work-orders?account={id}  → that managed account's work orders
GET /portal/referrals                 → the reseller's own partner referrals/rewards
```
Commissions/billing are **not** here — sub-owned, read from sub (§4.5 SoR note).

---

## 6. Unified event bus (the refactor)

One envelope replaces all bespoke pipelines (§1). Both directions, same shape, same reliability.

### 6.1 Envelope
```jsonc
{
  "id": "evt_01J...",                 // ULID — idempotency key
  "type": "work_order.en_route",      // catalog §6.3
  "spec_version": "1",                // envelope version
  "occurred_at": "2026-06-27T11:42:03Z",
  "source": "crm",                    // "crm" | "sub"
  "subject": { "type": "work_order", "id": "<uuid>" },
  "subscriber": { "crm_subscriber_id": "<uuid>", "sub_subscriber_id": "<uuid>" },
  "sequence": 42,                     // per-subject monotonic (ordering)
  "data": { /* typed per type, versioned with the catalog */ }
}
```

### 6.2 Transport & reliability
- **HTTPS POST** to the peer's `/events` receiver. Headers: `X-DotMac-Event`, `X-DotMac-Delivery`, `X-DotMac-Signature-256` (HMAC-SHA256 over raw body).
- **One secret per direction**: `DOTMAC_EVENT_SECRET_CRM_TO_SUB`, `DOTMAC_EVENT_SECRET_SUB_TO_CRM` (consolidates `CRM_WEBHOOK_SECRET` + `CRM_CHAT_WEBHOOK_SECRET`).
- **Outbox**: producers write the event in the *same DB transaction* as the state change; a dispatcher delivers post-commit. (CRM already commits-then-queues; sub already has push tasks — both become one outbox+dispatcher per side.)
- **At-least-once + idempotent consumer**: dedupe on `id` (consumer keeps a processed-id set / unique index).
- **Ordering**: apply if `sequence > last_seen[subject]`, else drop (stale) — last-write-wins per subject.
- **Retries + DLQ + redrive**: generalize `CrmSyncFailure` → `event_dead_letters` covering both directions; reuse `redrive_crm_dead_letters`.
- **Subscriptions**: reuse CRM `WebhookSubscription`; sub registers its `/events` endpoint for its catalog subset.

### 6.3 Catalog v1
**CRM → sub**
| Type | data | Consumer action |
|---|---|---|
| `chat.message_outbound` | conversation_id, preview | push (migrates today's flow) |
| `ticket.updated` | ticket_id, status, last_comment | upsert local ticket (replaces ticket *pull*) |
| `project.stage_changed` | project_id, stage, progress_pct | push + cache invalidate |
| `project.completed` | project_id | push |
| `work_order.scheduled` / `dispatched` / `en_route` / `started` / `completed` / `canceled` | work_order_id, scheduled_*, eta | push + cache invalidate |
| `referral.qualified` | referral_id | push |
| `referral.rewarded` | referral_id, reward_amount, currency | **credit wallet** (idempotent) + push |
| `quote.sent` / `quote.accepted` | quote_id, total | push |

**sub → CRM**
| Type | data | Consumer action | Replaces |
|---|---|---|---|
| `subscriber.changed` | profile/status fields, `reseller_id` | upsert CRM Subscriber (+ link, + child-org under reseller) | `push_subscriber_change` |
| `reseller.changed` | name, code, contact, status | upsert reseller `Organization(account_type=reseller)` (+ persist `crm_organization_id`) | (new) |
| `billing.snapshot_updated` | balance, dunning, next_charge_at | update CRM 360 snapshot | `push_crm_billing_snapshots` |
| `invoice.paid` | invoice_id, amount | timeline event; **referral qualify trigger** | (new) |
| `service.changed` | subscription/plan/status | update CRM service view | (part of subscriber sync) |
| `ticket.created` / `ticket.commented` | ticket_id, body | upsert CRM ticket | `crm_ticket_push` |

### 6.4 Migration (refactor, not additive)
1. Ship the envelope + `/events` receiver + outbox/dispatcher + `event_dead_letters` on **both** sides (behind a flag).
2. Re-point each bespoke task to **emit/consume the envelope** instead of its ad-hoc call — one flow at a time, oldest-and-safest first: `subscriber.changed`, then `billing.snapshot_updated`, `ticket.*`, finally `chat.message_outbound`.
3. Delete the per-flow client methods/tasks once its events run clean in prod (verify via DLQ depth + parity checks).
4. Net deletions: `crm_ticket_pull`, the bespoke `push_*` task bodies, `CRM_CHAT_WEBHOOK_SECRET`. `crm_client.py` keeps only request/breaker plumbing + the mint.

---

## 7. Reference vertical — Refer & Earn (exercises all four planes)

1. **Token**: app → sub `/me/portal/session` → sub mints `portal_token` (scopes `read/write:referrals`).
2. **Read**: app → CRM `GET /portal/referrals` → code + list + totals.
3. **Refer**: app → CRM `POST /portal/referrals {name,email,phone}` → wraps referral capture (`referral_service.capture`) → creates Lead (source=Referral) + Referral(pending).
4. **Qualify**: referred becomes active → CRM `referral.qualified`; (trigger may be the `invoice.paid`/`subscriber.changed` event arriving from sub).
5. **Reward**: admin/auto issue → CRM emits **`referral.rewarded`** on the bus.
6. **Credit**: sub consumer applies wallet credit (idempotent on `id`) + push "₦5,000 referral reward added." Replaces today's *logged-only* hook.

This proves: mint, Portal read + write, a CRM→sub event with a money side-effect, and a sub→CRM event as the qualify trigger.

---

## 8. Security checklist
- Portal token scoped to one `crm_subscriber_id`; CRM enforces row-level on every query.
- Customer-safe DTOs are explicit allow-lists, not "model minus fields."
- Event HMAC verified before processing; reject on skew/`spec_version` mismatch.
- `reward.rewarded` credit idempotent on event `id` (no double credit on redelivery).
- No PII/financials in push `data` beyond a short preview.

## 9. Rollout
1. Contracts merged (this doc + OpenAPI `portal.v1.yaml` + `events.v1.yaml`).
2. Event bus skeleton (envelope, receivers, outbox, DLQ) both sides, flagged.
3. **Refer & Earn** vertical end-to-end behind a flag → dogfood → GA.
4. Migrate bespoke flows onto the bus (§6.4); delete legacy.
5. Phase in Projects → Field Service → Quotes, each = Portal endpoints + catalog events, no new plumbing.

## 10. Open questions
- `portal_token` as JWT (stateless, CRM-verifiable) vs opaque (revocable via store)? Leaning JWT for parity with existing tokens.
- Per-subject `sequence` source on the sub side (logical clock vs updated_at)?
- ~~Do resellers get a portal token scoped to their managed accounts?~~ **Resolved (§4.4–4.5):** yes — `Reseller.crm_organization_id` ↔ CRM reseller `Organization`, managed accounts as the org subtree, `actor=reseller` token scopes to that subtree. Open sub-question: should `reseller.changed`/subtree placement be backfilled for existing resellers in one migration, or lazily on first mint?
