# Field Technician Mobile App — Scope

**Status:** Draft for sign-off · **Date:** 2026-06-10
**Decisions locked:** Flutter · in-house techs + vendor crews in v1 · offline cache + queued writes · Android + iOS together.

---

## 1. Product definition

One Flutter app, two login modes:

- **In-house technician** — a `Person` with a `TechnicianProfile` (`app/models/dispatch.py`), authenticating through the existing JWT flow (`POST /api/v1/auth/login` → access + refresh + optional TOTP MFA). Works **work orders**.
- **Vendor crew technician** — a `VendorUser` (per-person: unique `(vendor_id, person_id)`, `app/models/vendor.py`), today cookie-session only. Gets a new bearer-token auth path. Works **installation projects** and submits **as-built routes**.

The app shows each user only their module; shared core (auth shell, offline engine, camera, maps, push) is one codebase.

**Core promise:** a tech gets pushed a job, navigates there, executes it (timer, notes, photos, signature, equipment, materials), and completes it — even with no connectivity on site.

---

## 2. Architecture ground rules

These follow the repo's non-negotiable patterns (`.claude/rules/services.md`, `web-routes.md`):

1. **All business logic lives in `app/services/field/`.** API routes in `app/api/field/` are thin wrappers only — no `db.query()`, no branching logic, no notification sending in routes. Manager singleton pattern: static methods on classes, lowercase singleton exports, `ListResponseMixin` for lists, `coerce_uuid` / `validate_enum` / `apply_ordering` / `apply_pagination` / `get_or_404` from `app/services/common.py`.
2. **Reuse existing services — never duplicate them.** Transitions call `workflow.transition_work_order()`; events flow through the existing dispatcher; worklogs go through `timecost`; notifications through `NotificationService`. The field service layer *orchestrates and scopes*; it does not re-implement.
3. **Services commit** (`db.commit()` in the service, per CRM convention).
4. **Celery tasks orchestrate only** — push delivery, geocoding backfill, etc. delegate to services, import inside the task, return stats dicts, use `observe_job()`.
5. **Every new service ships with tests** (`tests/test_field_*.py`), using `db_session` and existing fixtures.

Proposed layout:

```
app/services/field/
    __init__.py        # singleton exports
    jobs.py            # FieldJobs — scoped listing, detail aggregation
    transitions.py     # FieldTransitions — wraps workflow.transition_work_order
    attachments.py     # FieldAttachments — upload, metadata, secured download
    worklogs.py        # FieldWorkLogs — wraps timecost, offline-batch aware
    materials.py       # FieldMaterials — view + consumption
    equipment.py       # FieldEquipment — ONT serial recording
    devices.py         # FieldDevices — push token registry
    schedule.py        # FieldSchedule — shifts + availability + jobs merged
    config.py          # FieldConfig — min app version, feature flags (DomainSetting)
app/services/push.py   # FCM HTTP v1 client + send-with-retry + token pruning
app/api/field/         # thin routers, one per service module
app/tasks/push.py      # Celery delivery task
```

---

## 3. Feature scope

### v1 (launch)

| # | Feature | Backend status today | New work |
|---|---------|----------------------|----------|
| 1 | Login + MFA + refresh; vendor login mode | Staff JWT flow done; vendor is cookie-only | Vendor bearer-token endpoints reusing TOTP infra |
| 2 | Push on assignment / schedule change / cancellation | Nothing (no FCM/APNs anywhere) | Full FCM pipeline; hook on existing `_notify_work_order_assignment_in_app` call sites (`app/services/workforce.py`) |
| 3 | My jobs — list + map, filters | CRUD API exists but unscoped | Technician-scoped `/field/jobs`; filters `assigned_to == me OR assignment member` |
| 4 | Job detail — customer, contact, address, scope, history, linked ticket/project | Data complete | Aggregated detail endpoint (one round trip), cost/rate fields excluded |
| 5 | Navigate to site | Addresses are free text on `Subscriber.service_address_*`; `WorkOrder.address_id` FK removed | **Location resolution service**: geocode on assignment (existing Nominatim service), cache lat/lng; graceful text-address fallback |
| 6 | Execution state machine: accept → en-route → start → complete / hold | `workflow.transition_work_order()` sets timestamps + SLA clocks; workforce service emits domain events | Transition endpoint wrapping the existing engine; GPS stamp; `WorkOrderEvent` rows; idempotency via `client_event_id` |
| 7 | Time tracking — auto timer, manual adjust, offline backdate | `WorkLog` + `timecost` service exist; no overlap validation | Mobile endpoints + server-side overlap/duration sanity checks; auto-stop on `hold` |
| 8 | Notes + photos with GPS/timestamp metadata | Notes exist; attachments are raw JSON lists; no WO upload endpoint | `FieldAttachment` model + multipart API; image resize + EXIF strip server-side |
| 9 | Customer signature on completion | Nothing | Signature capture (attachment `kind=signature` + signer name); **"signature unavailable" fallback path** (reason + premises photo) |
| 10 | Equipment recording — "installed ONT serial X" | `OntUnit`/`OntAssignment` exist but no subscriber link; `WorkOrderMaterial` has no serial field | `subscriber_id` link on `OntAssignment` (or junction) + record-serial step in completion wizard |
| 11 | Materials — view job items, mark used/leftover | Lifecycle stops at `issued`; `fulfilled` enum value unimplemented; no consumption tracking or stock decrement | `consumed_quantity` on items + fulfillment service method + stock decrement; view via `WorkOrderMaterial` |
| 12 | Offline: jobs cached, all actions queue and sync | Nothing | Client sync engine + server idempotency (unique `client_event_id` / `client_ref`) |
| 13 | Vendor module: my projects, detail, as-built submission (GPS route trace + photos + report) | `AsBuiltRoute` model + API exist (cookie auth); rejection requires fresh submission | Token auth; mobile capture flow; pre-fill resubmission from rejected route |
| 14 | My schedule — shifts, availability | `Shift`, `AvailabilityBlock` + API exist | Scoped read endpoint |
| 15 | App config endpoint — min app version, feature flags | `DomainSetting` exists; no public config endpoint | `GET /field/config` (unauthenticated, minimal payload) |

### v1.x (fast follows)

Location sharing to dispatcher (batched pings → `TechnicianLocation`, opt-in, shift-hours only) · daily summary dashboard · in-app call/SMS shortcuts · "on my way" customer notification (**mostly wiring** — `eta_notifications.py` exists; `send_work_order_completed_notification()` exists but is never called) · material request creation from the field.

### v2 backlog (explicitly out)

Wireless survey capture mode · offline map tiles · route optimization · barcode/QR material scanning · dispatcher live-map web view · team chat · expense logging · vendor quote editing · full bidirectional offline sync · fiber-path display (subscriber → OLT/PON/FDH — **no topology-to-subscriber model exists**; needs its own design) · i18n (English-only v1; no translation infra exists).

---

## 4. Backend workstream

### 4.1 New models (idempotent Alembic migrations, house conventions: UUID PKs, `is_active`, tz-aware timestamps)

```
FieldAttachment      id, work_order_id?, installation_project_id?, note_id?,
                     kind (photo|signature|document), storage_key, file_name,
                     mime_type, size_bytes, latitude?, longitude?, captured_at,
                     signer_name?, uploaded_by_person_id?/vendor_user_id?,
                     client_ref (uuid, UNIQUE — offline idempotency), is_active, timestamps

DeviceToken          id, person_id? | vendor_user_id? (XOR), platform (android|ios),
                     fcm_token (unique), app_version, last_seen_at, is_active

WorkOrderEvent       id, work_order_id, event (accepted|en_route|started|completed|held|…),
                     actor_person_id, latitude?, longitude?, occurred_at (client clock),
                     received_at (server clock), client_event_id (UNIQUE — idempotency),
                     payload JSON, created_at

TechnicianLocation   (v1.x) technician_id, lat, lng, accuracy_m, recorded_at, work_order_id?
```

Plus schema changes: `consumed_quantity` on material request / work-order-material items; `subscriber_id` on `OntAssignment` (or junction table); optional cached lat/lng for resolved job locations.

### 4.2 API surface — `app/api/field/`, mounted at `/api/v1/field`

All routes are thin wrappers; caller resolved from JWT (`require_user_auth` → person → `TechnicianProfile`) or vendor bearer token (→ `VendorUser`). Row scoping is enforced **in the service layer**, not the route.

```
GET   /field/config                            public: min app version, feature flags
POST  /field/devices                           register/refresh FCM token
GET   /field/me                                profile + today's counts
GET   /field/jobs?status=&from=&to=            my work orders
GET   /field/jobs/{id}                         aggregated detail bundle
POST  /field/jobs/{id}/transition              {event, occurred_at, lat?, lng?, client_event_id}
POST  /field/jobs/{id}/notes                   body + attachment refs
POST  /field/jobs/{id}/worklogs                start/stop pairs; accepts backdated offline entries
POST  /field/jobs/{id}/materials/consume       used / leftover quantities
POST  /field/jobs/{id}/equipment               record installed ONT serial
POST  /field/attachments                       multipart + metadata; returns id + secured URL
GET   /field/attachments/{id}                  AUTHENTICATED download (never /static)
GET   /field/schedule?from=&to=                shifts + availability + jobs merged

POST  /api/v1/vendor/auth/login | mfa | refresh   bearer tokens for VendorUser
GET   /field/projects                          vendor: my InstallationProjects
GET   /field/projects/{id}                     detail + notes + attachments
POST  /field/projects/{id}/as-built            GeoJSON LineString + photos + report
```

### 4.3 Transition service — integration contract (critical)

`FieldTransitions.apply()` must, in order:

1. Validate caller may transition (primary tech or `assigned_to`; helpers may not).
2. Check `client_event_id` — if seen, return the original result (idempotent replay).
3. Delegate to `workflow.transition_work_order()` — this is what sets `started_at`/`completed_at` and starts/stops **SLA clocks** (`app/services/workflow.py`). Do not set status directly.
4. Let the existing event dispatcher fire (`work_order_completed` → webhooks, notifications, **ERP sync** (10s dedup window), automation; survey Celery task polls completed WOs every 60s — all free if we use the real path).
5. Persist `WorkOrderEvent` (GPS, client + server timestamps).
6. Write an `AuditEvent` (service-layer writes bypass the web-route audit logging otherwise).
7. Enforce the **completion gate** (configurable per work type via `DomainSetting`): ≥1 photo and signature-or-fallback before `completed`.

### 4.4 Push pipeline

- `app/services/push.py` — FCM HTTP v1 (covers iOS via APNs; one server integration), token registry, retry, invalid-token pruning.
- Delivery via Celery task `app.tasks.push.send_push` (standard task pattern, `observe_job()`).
- Hooks at existing notification call sites: work-order assignment, reschedule, cancellation; vendor as-built review outcomes (event `variation_rejected` already emitted).
- Writes `Notification`/`NotificationDelivery` rows for auditability. **Note:** `NotificationService` has no duplicate-check today — push sends carry an idempotency key.
- Per-user quiet hours / mute preference (new, minimal: a `DomainSetting`-style per-person preference).

### 4.5 Security work (prerequisites, found in deep review)

| Item | Evidence | Action |
|---|---|---|
| `/static/uploads/*` served with no auth | `app/main.py` StaticFiles mount | Field photos/signatures go through `FieldAttachment` + authenticated download only. Never the static mount. |
| Deactivating a `Person` does not revoke sessions (fired tech keeps API access up to refresh TTL, 30 days) | `app/services/auth_dependencies.py` never checks `Person.is_active` | Add `is_active` check on auth + refresh; cascade session revocation on deactivation. **Blocker for launch.** |
| RBAC claims cached; role revocation inert until session re-creation | `app/services/auth_cache.py` | Invalidate auth cache on role change |
| Vendor portal auth skips `person.is_active` | `app/services/vendor_portal.py` | Same check on vendor token auth |
| `/api/v1/work-orders` is unscoped — any authenticated user can read/modify/DELETE any WO | `app/main.py` mounts workforce router with only `require_user_auth` | Add `require_permission("operations:work_order:*")` to admin surface; field surface enforces assignment scoping in service |
| Cost privacy | `WorkLog.hourly_rate` auto-resolves from `CostRate` | Field schemas exclude all rate/cost fields |
| Upload hygiene | per `.claude/rules/security.md` | Size check before write, UUID names, MIME allowlist, `resolve_safe_path()`; EXIF GPS extracted into columns then stripped |
| New role | — | `field_technician` role, minimal permission set; vendor tokens carry a `vendor` scope claim restricted to vendor endpoints |

---

## 5. Flutter app workstream

| Concern | Choice |
|---------|--------|
| State management | Riverpod |
| Local DB / offline cache | Drift (SQLite) **encrypted (SQLCipher)** — caches customer PII |
| HTTP | dio + interceptor for proactive JWT refresh (15-min access TTL) |
| Token storage | flutter_secure_storage |
| Push | firebase_messaging (+ local notifications foreground) |
| Maps / nav | google_maps_flutter; url_launcher handoff to Google/Apple Maps |
| Camera / signature | camera + compression (~1600px / ~300KB); signature pad widget |
| Background sync | workmanager (Android) / BGTaskScheduler (iOS) + connectivity-restored trigger |
| Crash reporting | Sentry or Crashlytics |

### Offline sync engine

- **Down-sync:** my jobs ±7 days into SQLite (detail bundles + customer phones) on login / pull-to-refresh; delta refresh on push receipt.
- **Up-sync outbox:** every mutation written locally first with a client UUID, flushed FIFO when online. Throttle ~1 req/s; honor 429 + `X-RateLimit-Reset` (global limit is 100 req/60s per user, `app/middleware/api_rate_limit.py`).
- **Conflict policy:** server wins on job *data*; client events are timestamped facts and append. Job reassigned/cancelled while offline → server returns a structured conflict; app shows a calm "job changed — review" state and **never discards captured evidence** (photos stay local until acknowledged).
- **Idempotency:** server unique constraints on `client_event_id` / `client_ref` make all retries safe.

### Screen map (~15 screens)

Login → (MFA) → **Today** (list/map toggle, sync badge, offline strip) → **Job detail** (details / activity / materials tabs; single-action bottom bar) → **Active job mode** (live timer, quick photo/note, materials checklist) → **Completion wizard** (checklist → evidence → equipment serial → sign-off w/ unavailable-fallback) → Schedule → Profile (sync queue status) → Vendor: Projects → Project detail → As-built capture (walk-record GPS trace + pinned photos) → Submission review.

Visual language: "Industrial Modern, outdoors" — Outfit/Plus Jakarta fonts, teal `#06b6d4` primary, work-type colors mapped from web domain colors (install=amber, repair=rose, survey=violet, maintenance=cyan), one status ramp shared with dispatch, 56dp primary targets, sunlight-first contrast, light+dark themes, calm sync/offline states (amber "3 items waiting", never red alarms). Full wireframes in the visual plan (see conversation record / to be added to `docs/design-guide.html` family if desired).

---

## 6. Edge-case register

**Offline/sync:** logout with non-empty queue (block w/ warning or encrypted retain) · device loss (encrypted DB + remote session revoke) · clock skew (server stores `received_at`, flags >15-min deltas) · multi-device same account (per-device tokens; outbox UUIDs make double-sync safe) · partial photo-batch upload failure (per-item retry, no batch abort).

**Execution:** customer absent/refuses signature → fallback path (reason + premises photo) · multi-day jobs (`hold` overnight; auto-stop worklog on hold) · two techs on one job (helpers log time/notes but cannot transition; server-side worklog overlap checks) · GPS/camera permission denied (app functions; transitions recorded without coordinates and flagged) · job with no resolvable coordinates (text address + Maps search handoff).

**Platform/ops:** ERP sync — mobile-created records leave `erp_id` null for the sync service to claim; rapid transitions coalesce inside ERP's 10s dedup window · ticket coupling is one-way (WO completion does not close the linked ticket — surfaced to dispatcher, not auto-closed) · vendor as-built rejection requires a fresh submission — app pre-fills from the rejected route · force-upgrade path via `/field/config` min-version.

---

## 7. Delivery plan

| Phase | Scope | Est. |
|-------|-------|------|
| **0 — Platform foundations** | FCM pipeline + `DeviceToken`; `FieldAttachment` + secured upload/download; vendor bearer auth; **session-revocation + `is_active` fixes**; workforce API permission fix; `field_technician` role; `/field/config` | 3–4 wks |
| **1 — Field service layer + API** | `app/services/field/*` + thin routers; transition contract (§4.3); location resolution; equipment recording; material consumption backend; worklog endpoints + validation; schedule; full pytest coverage | 4–5 wks |
| **2 — App core** (parallel after Phase 0) | Flutter shell, auth, encrypted offline engine, job list/map/detail, execution + completion flow, push handling | 6–8 wks |
| **3 — Vendor module** | Vendor login mode, projects, as-built capture/submission with resubmission pre-fill | 3–4 wks |
| **4 — Pilot & hardening** | 3–5 in-house techs + 1 vendor crew in production; battery/data profiling; store submissions (Apple review buffer); crash reporting | 2–3 wks |

**Total: ~19–23 engineer-weeks** (~4–5 calendar months with one backend + one Flutter dev in parallel). Deferring the vendor module to v1.1 saves ~3–4 weeks if timeline pressure appears.

---

## 8. Risks & open questions

- **iOS distribution:** App Store review needs demo credentials + 1–2 wks buffer; confirm Apple Developer account. TestFlight-only iOS for the pilot is a valid shortcut.
- **Photo storage growth:** local filesystem backend will grow fast; S3/MinIO backend (already supported via `STORAGE_BACKEND`) should be enabled before scale.
- **Material stock decrement:** inventory never decrements `quantity_on_hand` today — the fulfillment work touches warehouse process, not just code; needs ops sign-off.
- **Equipment model decision:** `subscriber_id` on `OntAssignment` vs. a new junction table — decide during Phase 1 design review.
- **Battery/data:** location pings deliberately deferred to v1.x and opt-in.

---

## 9. Phase 3 — Live location + voice capture (v1.x)

Phase 3 delivers the two strategic threads: **field-tech live location** (map, geofence
auto-status, nearest-tech assignment) and **voice→structured capture**.

### 9.1 Location model decision (task #41)

**Decision: a dedicated, _person-keyed_ field-tech location store — not a reuse of
`crm_agent_presence`.**

Rationale:
- **Keying.** Field jobs are assigned to `work_orders.assigned_to_person_id` (a `Person`)
  and the transition engine is person-keyed (`field_transitions.apply(db, person_id, …)`).
  CRM agent presence is keyed 1:1 to `crm_agents`, and a field tech is not necessarily a
  CRM agent. Keying the new store by `person_id` aligns with how the field domain already
  identifies a tech and lets geofence logic join directly to assigned work orders.
- **Separation of concerns.** Field movement has different semantics from support-agent
  presence: shift-scoped, geofence-driven, dispatch-facing (not inbox-facing), and a
  different retention posture. Overloading `crm_agent_presence` would couple two unrelated
  domains and force every field tech to own a CRM agent row.
- **Proven shape, copied not coupled.** The two-table design mirrors the battle-tested agent
  presence (`crm_agent_presence` + `crm_agent_location_pings`): a 1:1 current-snapshot row
  plus an immutable ping audit log with a retention prune. We copy the design, not the table.

Tables (task #42):
- `field_tech_presence` — one row per person: current lat/lng + accuracy, `last_location_at`,
  `last_seen_at`, `status` (`on_shift` / `on_break` / `off_shift`), `location_sharing_enabled`.
- `field_tech_location_pings` — immutable audit of every accepted ping, pruned on a retention
  window (default 72h), with lat/lng range check constraints.

Proximity (task #47) is computed in Python (haversine) over the small set of active,
sharing-enabled techs rather than PostGIS `ST_DWithin`, so the path is DB-agnostic and unit
testable; a PostGIS spatial index can replace it later if the active-tech set grows large.

### 9.2 Task map

| # | Task | Surface |
|---|------|---------|
| 42 | Location store + ingest endpoint + retention prune | Backend |
| 43 | Admin live-map field-tech feed (JSON) | Backend |
| 44 | Admin map UI: field-tech marker layer on the Leaflet live-map | Admin web |
| 45 | Shift-scoped adaptive location ping client | Flutter |
| 46 | Geofence auto-status via the field transition service | Backend |
| 47 | Nearest-tech auto-assignment + day routing | Backend |
| 48 | Voice→structured field-extraction AI use case | Backend |
| 49 | Voice capture → form pre-fill with confirm | Flutter |
| 50 | Voice quality gate: WER harness + confidence clamp | Backend |

Privacy posture: location is **opt-in per tech** (`location_sharing_enabled`) and only
collected while on shift; pings prune on the retention window; the admin feed shows only
sharing-enabled, non-stale techs — mirroring the agent live-map's privacy gate.
