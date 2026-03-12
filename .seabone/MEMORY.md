# Seabone Memory — dotmac_crm

## Project Facts

### From CLAUDE.md
> # CLAUDE.md - DotMac CRM Project Guidelines
> 
> This file provides guidance for Claude Code when working on this codebase.
> 
> ## Project Overview
> 
> DotMac CRM is an **omni-channel field service and CRM platform** for telcos/utilities:
> - **Backend**: FastAPI + SQLAlchemy 2.0 + PostgreSQL/PostGIS
> - **Frontend**: Jinja2 templates + HTMX + Alpine.js + Tailwind CSS v4
> - **Task Queue**: Celery + Redis
> - **Auth**: JWT + Cookies (multi-portal: admin, customer, reseller, vendor)
> - **Deployment**: Single-tenant (one instance per organization)
> - **Python**: 3.11+ (target 3.12)
> 
> ### Core Domains
> - **Tickets** - Customer support ticket management
> - **Projects** - Field service projects with tasks and templates
> - **Workforce** - Work orders, technician dispatch, scheduling
> - **CRM** - Conversations, inbox, leads, quotes, campaigns, omni-channel messaging
> - **Inventory** - Stock management, reservations, work order materials

### Stack Detection
- Build: pyproject.toml detected

## Known Patterns

### Cookie Security
- `app/csrf.py:37` — CSRF cookie always `secure=False` (hardcoded, not env-driven)
- `app/services/web_auth.py:201,213,290,393` — session_token + mfa_pending cookies always `secure=False`
- `app/services/auth_flow.py:749` — refresh token cookie IS configurable via `REFRESH_COOKIE_SECURE` env var or DB setting
- Fix pattern: add `cookie_secure: bool = bool(os.getenv("COOKIE_SECURE", ""))` to `app/config.py`

### Rate Limiting
- Global `APIRateLimitMiddleware` (100 req/min per IP/user) applied in `app/main.py`
- `/webhooks/*` and `/static/*` are exempt from global rate limiting
- Rate limiter blindly trusts `X-Forwarded-For` header (IP spoofing risk)
- Auth-level lockout exists in DB (`failed_login_attempts`, locks at 5)

### HTML Sanitization
- Inbox messages: `content_html` set only after `_sanitize_message_html()` in `app/services/crm/inbox/formatting.py`
- `| safe` in Jinja2 templates for message content is legitimate (pre-sanitized)
- Legal docs `document.content | safe` — admin-only, OK

### CSRF Implementation
- Double-submit cookie pattern in `app/csrf.py`
- Public survey POST endpoints lack CSRF validation (known gap)
- Admin portal: CSRF enforced via middleware

### Open Redirect Defense
- `_normalize_next_url()` in `app/services/crm/inbox/connectors_admin.py` validates paths start with `/` and not `//`
- `_sanitize_refresh_next()` in `app/services/web_auth.py` uses URL parsing with allowlist segments BUT has a bypass: `//attacker.com` starts with `/` so passes the startswith check; `urlparse("//attacker.com").netloc = "attacker.com"` but `.path = ""` so no segment matches — returns `//attacker.com` which browsers treat as `https://attacker.com`. Fix: also check `not next_url.startswith("//")`.
- `crm_inbox_comment_reply.py` validates with urlparse + `//` prefix check

### Webhook Security
- `/webhooks/crm/email` at `app/web/public/crm_webhooks.py:433` — NO auth, NO signature (high risk)
- `/webhooks/crm/whatsapp` at `app/web/public/crm_webhooks.py:310` — normalized payload fast-path bypasses HMAC (high risk)
- `/webhooks/crm/meta` — HMAC properly enforced before processing
- CRM inbox API webhooks (`/api/v1/crm/inbox/webhooks/*`) — protected by `require_user_auth` via `crm_router`

### File Upload Security
- MIME type validation across all upload handlers (messages, tickets, avatars, branding) uses `file.content_type` from multipart header — client-controlled, not magic-byte validated
- Stored filenames use `{uuid.hex}{Path(filename).suffix}` — preserves user-controlled extension
- `LocalBackend._safe_path()` in `app/services/storage.py:63` prevents path traversal via `.resolve().relative_to()`

### SSRF Risks
- `fetch_inbox_attachment()` in `app/services/crm/inbox/attachments.py:107` fetches `media_url` from DB metadata without domain allowlist — chained SSRF when webhooks are unauthenticated
- `geocoding.py:62` — `base_url` from admin DB settings (requires compromised admin)
- `splynx.py:33` — `base_url` from admin DB settings (requires compromised admin)

### Metrics / Observability
- `/metrics` endpoint at `app/main.py:484` is publicly accessible with no auth — exposes Prometheus data

### API Key Handling
- New API key creation at `app/web/admin/system.py:2536` redirects with `?new_key={raw_key}` in URL — key exposed in server logs

### HTTP Security Headers
- NO security headers set anywhere in the app (`X-Content-Type-Options`, `X-Frame-Options`, `CSP`, `HSTS`, `Referrer-Policy` all absent)
- Fix: add a Starlette middleware in `app/main.py` to set these on all responses

### Attachment Proxy
- `/admin/crm/inbox/attachment/{message_id}/{idx}` proxies external media with `Content-Disposition: inline` and remote Content-Type verbatim
- No MIME type allowlist for safe inline rendering → stored XSS risk if any attachment URL returns text/html or image/svg+xml
- Content-type comes from `media_response.headers.get("content-type")` in `app/services/crm/inbox/attachments.py:169`

### CSV Export Security
- `csv.DictWriter` in `reports.py`, `crm_contacts.py`, `subscribers.py` — no formula injection sanitization
- Field values starting with `=`,`+`,`-`,`@` can execute as Excel/LibreOffice formulas
- Formal finding: security-c8-4 (MEDIUM) — fix is to prefix affected cells with a tab character

### Inventory Race Condition
- `app/services/inventory.py:273` — `Reservations.create()` reads stock then increments `reserved_quantity` without `with_for_update`
- Concurrent requests can both pass the availability check and both commit → over-reservation beyond actual stock
- Formal finding: security-c8-1 (HIGH) — fix: add `.with_for_update(skip_locked=True)` to stock query

### Vendor Report File Serving
- `app/web/admin/vendors.py:787` — `FileResponse(path=as_built.report_file_path)` uses DB path directly
- No `Path.resolve().relative_to(base)` boundary check unlike the storage backend (`storage.py:63`)
- Formal finding: security-c8-2 (HIGH) — fix: add path boundary validation before FileResponse

### Scheduler Task Name Injection
- `app/schemas/scheduler.py:11` — `task_name` validated only for length (1–200 chars), no pattern/allowlist
- Any authenticated user can create a ScheduledTask with arbitrary `task_name` then enqueue it via `POST /scheduler/tasks/{id}/enqueue`
- `app/api/scheduler.py:60` passes `task.task_name` directly to `celery_app.send_task()`
- Formal finding: security-c8-3 (MEDIUM) — fix: add regex pattern + server-side allowlist in create()

### Deprecated API Usage (deps-c11)
- `datetime.utcnow()` deprecated since Python 3.12 — 2 occurrences: `app/web/admin/meta_oauth.py:217` and `app/web/admin/network.py:972`. Fix: `datetime.now(UTC)`. `network.py` already imports `UTC`; `meta_oauth.py` needs it added.
- `pydyf` (declared dep) ships no `py.typed` marker — forces `# type: ignore[import-untyped]` at `app/services/pdf_utils.py:17` and `app/web/admin/crm.py:104`. Centralise in `mypy.ini` under `[mypy-pydyf]` instead.

### Undeclared Direct Dependencies
- `requests` (2.32.5) — used in `app/services/splynx.py` (8 inline imports) and `app/tasks/subscribers.py` (1 inline import) but NOT in pyproject.toml. Only present transitively via `opentelemetry-instrumentation-requests`. Removing OTel package would silently break Splynx integration.
- `email-validator` (2.3.0) — required by Pydantic `EmailStr` used in 6+ schema files, not declared in pyproject.toml. Only present transitively via pydantic extras.

### Ghost Dependencies (declared but unused)
- `shapely = "2.0.4"` — zero imports in `app/` or `scripts/`. Dead dep from removed GIS domain.
- `ncclient = "0.6.15"` — zero imports anywhere. Dead dep from removed NAS domain.
- `routeros-api = "0.17.0"` — zero imports anywhere. Dead dep from removed NAS domain. Has `ignore_missing_imports` in mypy.ini (line [mypy-routeros_api.*]) that will trigger `warn_unused_ignores` once removed.
- `pyrad = "2.4"` — zero imports in `app/`. Only mock classes exist in `tests/mocks.py:73-100` (can be deleted).
- `paramiko = ">=3.5.0"` — zero imports in `app/`. Was a transitive dep of ncclient; declared directly.

### Dead Code — NAS Domain
- `app/services/scheduler_config.py:255` registers `app.tasks.nas.cleanup_nas_backups` — the NAS domain was removed but this beat task was not cleaned up
- `app/tasks/nas.py` does NOT exist; task defaults `enabled=True` via `NAS_BACKUP_RETENTION_ENABLED` → Celery `NotRegistered` errors on every beat cycle
- Also uses `SettingDomain.catalog` (removed domain) for the lookup at line 238

### Service Layer Violations — operations.py
- `app/web/admin/operations.py` (1314 lines) has 22+ direct `db.query()` calls across route handlers: `sales_orders_list`, `work_orders_list`, `technicians_list`, `dispatch_dashboard`
- Stats queries (5× COUNT per status value), filter queries, and pagination are all done in-route instead of via service managers
- This is the main architectural violation file — all other web routes properly delegate to services

### Untested Critical Services
- `app/services/workflow.py` (960 lines) — SLA engine, state machines — zero tests
- `app/services/automation_actions.py` (554 lines) — automation execution engine — zero tests
- `app/services/automation_rules.py` (210 lines) — rule evaluation — covered by `test_automation_conditions.py` but not actions
- 99/108 service files have no direct test file; key operational services are tested indirectly via integration tests

### Long Functions — Key Offenders
- `app/services/crm/inbox/_core.py:689 send_message()` — 302 lines (worst in codebase)
- `app/services/tickets.py:641 Tickets.update()` — 203 lines
- `app/services/meta_webhooks.py:516 process_messenger_webhook()` — 190 lines (near-duplicate of process_instagram_webhook at line 709)
- `app/services/scheduler_config.py:194 build_beat_schedule()` — 590 lines (config function, not logic)

### API Contract Patterns (updated api-cycle10 scan)
- **Core domain routers are well-formed**: tickets, dispatch, CRM conversations/contacts/messages, analytics, audit, workflow all consistently use `response_model=`, `ListResponse[T]`, `Query(ge=..., le=...)`, and correct `status_code` on mutations
- **Newer/specialised routers have gaps**: `app/api/ai.py` (**8** endpoints no response_model), `app/api/data_quality.py` (4 endpoints), `app/api/performance.py` (5 endpoints), `app/api/projects.py` (5 view endpoints), `app/api/fiber_plant.py` (5 endpoints — **all** untyped), `app/api/sales.py` (5 kanban/report endpoints — **all** untyped), `app/api/crm/widget_public.py` (2 session endpoints)
- **CRM router auth is set at include_router level**: `app/main.py:395` uses `dependencies=[Depends(require_user_auth)]` on `_include_api_router(crm_router, ...)` — individual CRM endpoints don't need per-endpoint auth; the router-level dep covers all
- **All other routers also auth at include level**: fiber_plant (394), sales (396), nextcloud_talk (400), bandwidth (401), scheduler (386) — all mounted with `dependencies=[Depends(require_user_auth)]` in main.py
- **Inline role check antipattern**: `app/api/bandwidth.py:162` `get_top_users` uses `if current_user.get('role') != 'admin': raise 403` instead of `require_permission()` — only instance found; fix with permission dep on decorator
- **POST with query params antipattern** (multiple instances): `app/api/performance.py:67` `generate_review`; `app/api/material_requests.py:71,75` approve/reject (person IDs in logs); `app/api/wireless_survey.py:143` analyze-los (point IDs in logs); `app/api/vendors.py:104` as-built accept (reviewer_id in logs) — all should use request body schemas
- **Untyped webhook body**: `app/api/subscribers.py:207` accepts `payload: dict` for multi-system webhook normalization — no schema validation on any payload field
- **SSE endpoints**: `app/api/bandwidth.py:101,216` use `EventSourceResponse` but missing `response_class=EventSourceResponse` in decorator — shows wrong content-type in OpenAPI
- **Service layer violation in public API**: `app/api/crm/widget_public.py:575,614` contains direct `db.query(Message)` calls in route handler; should be extracted to service method
- **Zero API fixes landed since cycle-3**: None of the 8 cycle-3 findings have been resolved; response_model coverage is actively declining as new endpoints are added without schemas
- **Global search endpoint missing response_model**: `app/api/search.py:93 /search/global`
- **5 dead search stubs**: `/search/subscriptions`, `/search/invoices`, `/search/nas-devices`, `/search/catalog-offers`, `/search/accounts` always return empty via `_empty_typeahead()` — should be deleted
- **Bare dict response_model antipattern**: `app/api/gis.py:205` uses `response_model=dict`, `app/api/nextcloud_talk.py` uses `response_model=dict` / `list[dict]` across 10 endpoints
- **No N+1 patterns found in API handlers** — service layer rule is consistently followed in routes
- **IDOR on agent presence**: `app/api/crm/presence.py:75` `upsert_agent_presence` has no per-agent auth check — any authenticated user can set any other agent's status; sibling `upsert_agent_location_presence` at line 48 correctly uses `_can_write_agent_location()` guard
- **Wireless survey list missing ListResponse**: `app/api/wireless_survey.py:44` uses `list[WirelessSiteSurveyRead]` not `ListResponse[WirelessSiteSurveyRead]` — no pagination metadata
- **Unconstrained pagination params**: `app/api/crm/inbox.py:74` `list_templates` has `limit: int = 100, offset: int = 0` with no `Query(ge=..., le=...)` bounds
- **Missing response_model on GIS sync**: `app/api/gis.py:364` `POST /gis/sync` has no `response_model`
- **Missing response_model on inbox webhooks**: `app/api/crm/inbox.py:146,152` webhook handlers return `{"status": "ok"}` with no schema

### Exception Handling Patterns
- Many `except Exception: continue` without logging in bulk operation loops — `web_quotes.py:960,979`, `dotmac_erp/technician_sync.py:193`, `agent_mentions.py:119,126,133`
- `except Exception: pass` with comments like "best-effort" in notifications are INTENTIONAL and acceptable
- `except Exception: pass` in `_safe_json()` in `performance/reviews.py` is intentional fallback
- UUID coerce failures silently continue — this pattern is widespread and generally acceptable since UUIDs come from trusted internal sources

### WebSocket Security
- `app/websocket/auth.py:16` — JWT token accepted via URL query param `?token=` (exposes to server logs/history); cookie fallback already exists but query-param path should be removed
- `app/websocket/widget_auth.py:21` — Same issue for visitor token
- `app/websocket/router.py:61` — `SUBSCRIBE` message handler calls `manager.subscribe_conversation(user_id, conversation_id)` with NO DB access check; any authenticated user can subscribe to any conversation (IDOR)
- WebSocket auth in `authenticate_websocket()` validates JWT correctly; the issue is token transport, not validation

### JWT Settings Caching
- `app/services/auth_flow.py:136` — `_load_jwt_settings_cache()` caches JWT secret once per process lifetime (`_JWT_SETTINGS_CACHED = True`) with no TTL
- Env var `JWT_SECRET` is the primary source (preferred over DB); DB rotation alone won't affect running processes anyway
- Fix: add timestamp-based TTL (5-10 min) so DB-stored secret rotations propagate without restart

### Scheduler API Authorization
- `app/api/scheduler.py` — All 7 endpoints only require `require_user_auth` (any authenticated user); no fine-grained `system:scheduler:write` permission
- `enqueue_scheduled_task` calls `celery_app.send_task(task.task_name, ...)` — task name comes from DB (trusted), not user input; args/kwargs also from DB
- Router is correctly mounted with `require_user_auth` at include level in `app/main.py:386`

### Password Reset Token Exposure
- `app/services/web_auth.py:238` — Force-password-reset flow after login embeds raw 60-min reset token in `?token=` redirect URL
- Normal "forgot password" flow sends token only via email (safe)
- Only triggered when admin sets `PASSWORD_RESET_REQUIRED` flag or credential is expired

### `_ensure_person` Duplication (quality-c6-1)
- Identical 3-line helper duplicated across 8 service files: `tickets.py:219`, `dispatch.py:39`, `workforce.py:34`, `projects.py:609`, `vendor.py:66`, `auth.py:93`, `sales_orders.py:34`, `timecost.py:26`
- Pattern: `if not db.get(Person, coerce_uuid(person_id)): raise HTTPException(404, "Person not found")`
- Consolidation target: `app/services/common.py` — add `ensure_person(db, person_id) -> Person`

### Silent Exception Swallowing (quality-c6 scan)
- `app/web/admin/crm_inbox_message.py:128` — `except Exception: pass` on mention notification (no logging)
- `app/web/admin/tickets.py:2140` — `except Exception: pass` on comment mention notification (no logging)
- `app/services/crm/web_quotes.py:960,979` — `except Exception: continue` in bulk ops with no error tracking
- `app/services/crm/shifts.py:59` — `except Exception: pass` (no logging)
- `app/services/crm/web_leads.py:225,554` — `except Exception: pass` on person lookup (no logging)
- Pattern distinction: `except Exception: pass` in telemetry/observability is INTENTIONAL; in notification paths it is NOT

### Compatibility Wrapper Files (quality-c6 scan)
- 11 files in `app/services/crm/` are pure re-export shims: `inbox_normalizers.py`, `inbox_inbound.py`, `inbox_parsing.py`, `inbox_dedup.py`, `inbox_outbound.py`, `inbox_connectors.py`, `inbox_connectors_create.py`, `inbox_contacts.py`, `inbox_queries.py`, `inbox_polling.py`, `conversation.py`
- All still actively imported by consumers; canonical paths are `app/services/crm/inbox/` subpackage
- Removing requires updating ~15 import sites

### Discarded Expression Bug (quality-c6-5)
- `app/services/projects.py:704` — `html.escape(_person_label(created_by)) if created_by else None` result is never assigned
- This is almost certainly a missing `creator_name =` assignment — the creator name is absent from all project task assignment emails

### Hard Delete Inconsistency (quality-c6-8)
- `TicketComment.delete()` (`tickets.py:969`) and `TicketSlaEvent.delete()` (`tickets.py:1027`) use `db.delete()` (hard delete)
- All `Ticket` create/update operations use soft delete (`is_active=False`)
- This is inconsistent within the same domain module

### Celery Task Observability Gap (quality-c9-5)
- Only 5 task files have `observe_job()` instrumentation: `subscribers.py`, `integrations.py`, `gis.py`, `oauth.py`, `crm_inbox.py`
- 9 task files are MISSING `observe_job()`: `notifications.py`, `bandwidth.py`, `campaigns.py`, `surveys.py`, `workflow.py`, `webhooks.py`, `performance.py`, `intelligence.py`, `events.py`
- `observe_job` is defined in `app/metrics.py` (NOT `app/telemetry.py` — crm_inbox.py uses telemetry path which is an alias)

### N+1 in Notification Delivery (quality-c9-1)
- `app/tasks/notifications.py:144` — `db.get(ConnectorConfig, ...)` called per WhatsApp notification in batch loop
- `app/tasks/notifications.py:165` — campaign join query also per notification in same loop
- Both should be pre-loaded before the loop and looked up from a dict

### Widget Public API Service Layer Violation (quality-c9-2)
- `app/api/crm/widget_public.py:512,575,614` — 3 direct `db.query(Message)` calls in route handlers
- Extends the known `operations.py` service-layer violation to a second file

### Second Discarded Expression Bug (quality-c9-3)
- `app/tasks/bandwidth.py:222` — `minute_start + timedelta(minutes=1)` result never assigned in `aggregate_to_metrics()`
- Same pattern as `projects.py:704`; ruff rule B018 (found-useless-expression) not currently enabled

### Disabled No-Op Celery Task (quality-c9-4)
- `app/tasks/crm_inbox.py:37-41` — `send_reply_reminders_task` is registered but always returns 0 (temporary operational safety switch)
- Should be disabled in scheduler config or removed, not left as a silently vacuous registered task
