# Technician Coordination — Spec

**Status:** Draft · **Repos:** `dotmac_crm` (field app + backend, owner), `dotmac_sub`
(customer app + backend) · **Depends on:** live technician tracking (shipped),
work-order mirror, the selfcare chat-webhook push pattern.

---

## 1. Problem & goal

During an in-progress field visit, the customer's real needs are **logistics, not
support**: "how far away?", "I'm at the back gate / unit 4B", "call when you
arrive", "I stepped out 10 min". Today there is no channel for this — the
customer can see the technician on the live map, but can't coordinate, and the
technician can't signal status. The gap costs **failed visits / wasted truck
rolls** (the #1 field-service cost) and erodes confidence.

**Goal:** a lightweight, **visit-scoped** coordination channel that raises
customer confidence without burdening the technician.

## 2. Principles

1. **Visit-scoped & time-boxed.** Exists only while the work order is
   `in_progress` (the same Start-Work→End-Work gate the live map uses).
   Auto-closes on completion/cancel. No persistent line.
2. **Structured, not free-form.** The technician taps **canned statuses**, never
   types. The customer gets bounded actions (a callback request, a few chips) —
   not open text to a named individual.
3. **Async-framed.** "Emeka may reply between tasks" — no live-typing pressure.
4. **Dispatch fallback.** Technician offline/unavailable → routes to the office,
   never a void.
5. **Minimal identity.** First name + role only; no phone-number leak.
6. **Off-topic → support.** Anything non-logistics routes to the existing
   customer↔agent support chat.

## 3. Non-goals

- **Not** free-form two-way chat.
- **Not** a persistent customer↔technician relationship or history beyond the visit.
- **Not** a support channel (billing/plan/speed questions go to support).
- **Not** built on the CRM Conversation/agent-inbox system (keep it off support's plate).

## 4. User stories

- **Customer:** "While my technician is on the way, I can see status updates and
  ask them to call me — without phoning the office."
- **Technician:** "I tap one button to tell the customer I'm on my way / arrived
  / running late, and I see if they've asked for a callback — without stopping
  my work to type."
- **Dispatch:** "If a technician can't respond, the customer's request reaches me
  so no one is left hanging."

## 5. Phasing

Ship value early; add later phases only if usage warrants.

### Phase 1 — Technician → Customer status pings  *(build first; highest value, lowest risk)*
The technician taps a canned status in the field app; the customer gets a push
and sees it on the tracking screen / visit banner.

### Phase 2 — Customer → Technician "Request a callback"  *(one bounded action)*
The customer taps *Request a callback* (+ optional chips: *I'm home · Use back
gate*); it reaches the assigned technician's field app, with dispatch fallback.

### Phase 3 (optional) — a few canned customer→technician chips
Only if Phase 2 usage shows customers need more than a callback. Never open text.

---

## 6. Design

### 6.1 Data model (CRM)
A lightweight **`WorkOrderCoordination`** event (own table, *not* the Conversation
system):

| Field | Notes |
|---|---|
| `id` | uuid pk |
| `work_order_id` | FK; the visit it belongs to |
| `direction` | `tech_to_customer` \| `customer_to_tech` |
| `kind` | enum: `on_my_way` · `arrived` · `running_late` · `confirm_access` · `callback_request` · `chip` |
| `body` | optional short text for chips (never free tech input) |
| `author_person_id` | technician (tech→cust) or null/customer ref (cust→tech) |
| `created_at` | |

Gated in the service layer: only accepted while the work order is `in_progress`.

### 6.2 Endpoints
- **CRM** `POST /field/work-orders/{id}/coordination` — technician posts a status
  ping (permission-gated to the assigned tech). Service validates the
  `in_progress` window, writes the event, and fans out (see push).
- **Sub** `POST /me/work-orders/{id}/coordinate` — customer requests a callback /
  sends a chip. Proxies to the CRM (via `selfcare`/`crm_client`), scoped to an
  owned work order.
- **Reads:** the latest coordination events ride the existing work-order payloads
  (`/me/work-orders` on the sub; the field app's job detail on the CRM) — no new
  polling surface needed for Phase 1.

### 6.3 Push (reuses existing patterns)
- **Tech → Customer:** mirror `selfcare.notify_chat_message` — a new
  `selfcare.notify_work_order_ping(subscriber_id, work_order_id, kind, text)` →
  signed webhook to the sub → `push.send_push(type: "work_order_ping")`. The
  mobile push router already keys on `work_order_*`; route to the tracking screen.
- **Customer → Tech:** the CRM pushes the assigned technician's field-app device
  (its FCM already handles `work_order_assigned`; add a `coordination` type
  routing to the job-detail screen). Dispatch fallback when the tech has no
  active device / is off-shift.

### 6.4 Mobile UX
- **Field app** (`dotmac_crm/mobile`, JobDetailScreen): a row of one-tap status
  buttons (*On my way · Arrived · Running late · Confirm access*), shown only for
  the tech's own in-progress work order. Phase 2 adds an inbound "callback
  requested" banner.
- **Customer app** (`dotmac_sub/mobile`, TechnicianTrackScreen / visit banner):
  render the latest ping ("Emeka: On my way · ~15 min"); Phase 2 adds a *Request
  a callback* button + chips. Ephemeral — clears when the visit ends.

### 6.5 Gates & lifecycle
- Open when the work order enters `in_progress`; **auto-close** on
  `completed`/`canceled`. Reuse the exact gate the live-location provider uses.
- Post-visit: coordination is read-only history for a short window, then dropped
  from the customer UI (not a persistent thread).

## 7. Reuse map (why it's cheap)

| Needs | Reuses |
|---|---|
| Visit gate | Start-Work→End-Work / `in_progress` (live-location gate) |
| Customer-facing surface | TechnicianTrackScreen + Home visit banner (shipped) |
| Tech→customer push | `selfcare.notify_chat_message` webhook pattern (shipped) + sub chat receiver |
| Tech-side push | Field-app FCM + `work_order_assigned` routing (exists) |
| Work-order data | `work_orders_mirror` (sub) + WorkOrder (CRM) |

## 8. Security & privacy
- Technician identity limited to **first name + role** on the customer side.
- No phone numbers exchanged.
- Customer actions are **bounded** (canned kinds), so no abusive free text to a
  named person.
- Off-topic → existing support chat.
- All endpoints scoped: tech to their assignment, customer to their owned work
  order.

## 9. Acceptance criteria

**Phase 1**
- Tech taps a status on an in-progress job → customer receives a push and sees
  the ping on the tracking screen within seconds.
- No ping accepted for a work order that isn't `in_progress`.
- Pings disappear from the customer UI once the visit completes.

**Phase 2**
- Customer taps *Request a callback* → the assigned tech's field app shows it;
  if the tech has no active device, dispatch is notified instead.

## 10. Effort & sequencing
- **Phase 1:** one coordinated change — CRM (model + endpoint + notify) · sub
  (webhook receiver + tracking-screen render) · field app (quick-action buttons).
  Moderate, high reuse. **Ship, then observe real usage.**
- **Phase 2:** smaller (customer button + proxy + tech-app inbound).
- **Phase 3:** only if warranted.

## 11. Open questions
- Exact canned-status set (start minimal: on-my-way / arrived / running-late /
  confirm-access).
- Dispatch-fallback target (a team queue vs. a specific coordinator).
- Retention: how long coordination history is visible post-visit.

## 12. Out of scope
- Free-form chat; persistent threads; group chat; media attachments.
- Technician↔support-agent messaging (separate concern).
