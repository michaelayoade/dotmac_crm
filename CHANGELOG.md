# Changelog

All notable changes to DotMac CRM are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [2026-02-28]

### Security
- **Audit scan (cycle 8)**: Sentinel scan identified 4 new findings — inventory stock reservation race condition (HIGH, `app/services/inventory.py:273`, `security-c8-1`), vendor as-built report path traversal via `FileResponse` with unvalidated DB path (HIGH, `app/web/admin/vendors.py:787`, `security-c8-2`), scheduler task name injection allowing any authenticated user to enqueue arbitrary registered Celery tasks (MEDIUM, `app/schemas/scheduler.py`, `security-c8-3`), CSV formula injection in reports and CRM contacts export (MEDIUM, `security-c8-4`)

### Dependencies
- **Dependency audit (cycle 8)**: 6 new findings — `idna<3.7` vulnerable to CVE-2024-3651 ReDoS via any user-supplied hostname (HIGH, `deps-c8-1`), PyJWT 2.3.0 vulnerable to CVE-2022-29217 algorithm confusion via python-jose transitive dep (HIGH, `deps-c8-2`), `urllib3<1.26.17` CVE-2023-43804 cookie injection + CVE-2023-45803 (MEDIUM, `deps-c8-3`), bcrypt 4.x/passlib 1.7.4 compatibility break causing silent hash verification failures (MEDIUM, `deps-c8-4`), outdated MarkupSafe 2.0.1 (LOW, `deps-c8-5`), outdated Poetry 1.8.4 / pip 22.0.2 toolchain (LOW, `deps-c8-6`)
- **Dependency audit (cycle 11)**: 3 new findings — `datetime.utcnow()` deprecated since Python 3.12, used in `app/web/admin/meta_oauth.py:217` and `app/web/admin/network.py:972` (MEDIUM, `deps-c11-1`, `deps-c11-2`); `pydyf` ships no `py.typed` marker forcing per-file `# type: ignore` instead of centralised `mypy.ini` suppression (LOW, `deps-c11-3`); 13 total open dependency findings across c4/c8/c11

### Quality
- **Code quality audit (cycle 9)**: 6 new findings — N+1 in notification delivery: `ConnectorConfig` and campaign join queries per-notification inside WhatsApp batch loop (HIGH, `quality-c9-1`, `app/tasks/notifications.py:144`); service layer violation: 3 direct `db.query(Message)` calls in `app/api/crm/widget_public.py` route handlers (HIGH, `quality-c9-2`); discarded expression in `app/tasks/bandwidth.py:222` — `minute_start + timedelta(minutes=1)` result never assigned (MEDIUM, `quality-c9-3`); disabled no-op Celery task `send_reply_reminders_task` always returns 0 but remains registered (MEDIUM, `quality-c9-4`); 9 of 14 task files missing `observe_job()` instrumentation (MEDIUM, `quality-c9-5`); `BandwidthSample` model missing `updated_at` timestamp column (LOW, `quality-c9-6`); codebase health 57/100
- **API contract audit (cycle 10)**: 8 new findings — IDOR on `upsert_agent_presence`: any authenticated user can set any agent's presence status, no per-agent auth check (`app/api/crm/presence.py:75`, HIGH, `api-c10-1`); POST-with-query-params antipattern on `material_requests` approve/reject, `wireless_survey` analyze-los, and `vendors` as-built accept endpoints (MEDIUM, `api-c10-2/3/4`); wireless survey list missing `ListResponse` pagination wrapper, `gis.py` POST sync missing `response_model`, inbox templates missing pagination bounds (`Query(ge=..., le=...)`), inbox webhook handlers missing `response_model` (LOW, `api-c10-5/6/7/8`); codebase health 55/100

---

## [2026-02-27]

### Security
- **Cookie Secure Flags**: CSRF cookie and session/MFA cookies were hardcoded `secure=False`; now controlled by `COOKIE_SECURE` env var (default off, set to `true` in production) — `app/config.py`, `app/csrf.py`, `app/services/web_auth.py` (PR #8)
- **Jinja2**: Upgraded to `>=3.1.6` to fix sandbox escape CVEs CVE-2024-56201 and CVE-2024-56326 (PR #2)
- **cryptography**: Upgraded to `>=44.0.0` to fix GHSA-h4gh-qq45-vh27 and multiple high-severity CVEs in 42.x (PR #2)
- **paramiko**: Upgraded to `>=3.5.0` for SSH host key verification improvements (PR #4)
- **weasyprint / pydyf**: Upgraded to `>=64.0` / `>=0.11.0` to reduce PDF generation attack surface
- **Survey CSRF**: Public survey POST endpoints (`/s/{slug}/submit`, `/s/t/{token}/submit`) now require CSRF token validation — prevents cross-site form submission on unauthenticated survey pages (PR #9)
- **Rate Limiter IP Spoofing**: `APIRateLimitMiddleware` no longer blindly trusts `X-Forwarded-For`; only used when `request.client.host` is in the `TRUSTED_PROXIES` allowlist (PR #10)
- **Webhook Rate Limiting**: `/webhooks/crm/*` endpoints now subject to a dedicated per-IP rate limit (60 req/min) separate from the global API limiter (PR #11)
- **Path Traversal in Avatar Delete**: `delete_avatar()` now verifies the resolved file path is inside the upload directory before calling `unlink()` — `app/services/avatar.py` (PR #12)
- **Weak Hash in ERPNext Importer**: `hashlib.md5()` replaced with `hashlib.sha256()` for subscriber ID generation in `app/services/erpnext/importer.py` (PR #13)
- **Prometheus Metrics Auth**: `/metrics` endpoint now optionally gated behind `Authorization: Bearer <METRICS_TOKEN>` — set `METRICS_TOKEN` env var to enable; empty = public (backward-compatible default) (PR #14)
- **JSON Input Validation**: Raw `json.loads()` calls for `tasks_json` and `mentions` fields in admin project routes replaced with Pydantic `TypeAdapter.validate_json()` — returns HTTP 400 on invalid input (PR #16)

### Fixed
- **poetry.lock**: Regenerated lock file to resolve CI failure caused by `pyproject.toml` changes from multiple merged dependency PRs (PR #15)

### Changed
- `.env.example`: Documents `COOKIE_SECURE=true`, `TRUSTED_PROXIES`, and `METRICS_TOKEN` env vars for production deployments
- **Frontend Audit**: Responsive, accessibility, and visual consistency improvements across survey templates (`admin/surveys/detail.html`, `admin/surveys/form.html`, `public/surveys/`) and error pages (`errors/404.html`, `errors/500.html`, `domain.html`) — WCAG 2.1 AA compliance pass, dark mode variants, touch targets, and Industrial Modern design system alignment (PR #17)

---

## [2026-02-24]

### Added
- **CI/CD**: GHCR Docker image build and push workflow — builds and tags `ghcr.io/michaelayoade/dotmac_crm` on every push to `main` (`:sha` + `:latest`)
- **CRM Inbox**: Conversation priority levels (none / low / medium / high / urgent) with dropdown selector, badge display, sortable column, and filter support
- **CRM Inbox**: Conversation mute toggle — suppresses notifications for silenced threads
- **CRM Inbox**: Auto-resolve idle conversations — configurable days threshold via system settings
- **CRM Inbox**: Email transcript export for conversations
- **CRM Inbox**: Canned response shortcodes — triggered with `/` in the composer
- **CRM Inbox**: Keyboard shortcuts (`r`=reply, `e`=resolve, `j`/`k`=navigate, `?`=help overlay)
- **CRM Inbox**: Audio notification alerts replacing synthesized browser beep
- **CRM**: Reseller portal with multi-account access
- **CRM**: Filter engine for conversation list
- **CRM**: Campaign permissions framework
- **CRM**: Organization membership management
- **CRM**: Presence tracking for agents
- **CRM**: Fiber QA checks
- **CRM**: Conversation macros (one-click multi-action templates)
- **CRM**: CSAT surveys for closed conversations

### Fixed
- CI pipeline fully green: mypy errors resolved, bandit config corrected
- Ruff upgraded to 0.15.x for consistent lint enforcement across CI and local dev
- Ruff lint and format issues remediated across `app/` and `tests/`
- `poetry.lock` regenerated to match `pyproject.toml` dependency declarations
- Test infrastructure: enum backfill, Redis cache isolation, FK constraint handling fixed

---

## [2026-02-23]

### Fixed
- **CRM Inbox**: Messages now reach agents immediately — sync→async bridge in WebSocket broadcaster fixed for Celery workers
- **CRM Inbox**: Stale "new message" banners replaced with automatic conversation list refresh
- **CRM Inbox**: Selected conversation highlight correctly restored after HTMX list swap
- **Performance**: N+1 queries in `post_process_inbound_message` resolved via batch agent loading

### Security
- Server-side password strength validation added to registration form
- CSRF token enforcement added to registration form
- CC email addresses now validated with `EmailStr` and capped at 20 recipients
- Hardcoded S3 credentials removed from config defaults
- Fiber asset merge now uses row-level locking (`SELECT FOR UPDATE`) to prevent race conditions
- Location ping pruning throttled to at most every 5 minutes
- `reports/` added to `.gitignore` to prevent accidental PII leaks

---

## [2026-02-19]

### Changed
- **CRM Refactor**: Extracted all CRM inbox routes into dedicated router modules (presence, settings, message, assignment/resolve, conversation display, comment partials, new-conversation, status/resolve-gate, private-note/attachment, catalog/redirect, connector actions, social comment reply)
- **CRM Refactor**: CRM contact routes extracted into dedicated router module; business logic moved to service layer
- **CRM Refactor**: Lead, quote, sales, and widget routes extracted with logic moved to service layer
- **CRM Refactor**: Inbox conversation thread detail, contact detail sidebar, conversations partial, routing rules, template admin, and bulk agent update — all moved to service layer
- **CRM Campaigns**: All campaign page context builders (list, detail, steps, form, preview, recipients table, audience preview, WhatsApp template lookup, step payloads, schedule/send/cancel, create/update upsert, delete) extracted into web service layer

### Fixed
- Async queue enqueue failures in web/API routes now handled gracefully (non-fatal)
- Webhook enqueue path hardened; Redis ACL runtime config corrected
- Storage backend restricted to local-only; remote S3 security checks applied

---

## [2026-02-16]

### Fixed
- **Fiber Maps**: N+1 query eliminated in map module via batch loading
- **Fiber Maps**: Soft deletes applied correctly across fiber asset operations
- **Fiber Maps**: Security review applied; direct DB calls moved to service layer

---

## [2026-02-15]

### Added
- **Data Quality**: Data quality management module with AI-powered quality gate (completeness, consistency, accuracy scoring per entity)
- **Tables**: Column chooser for contacts, vendors, campaigns, and subscribers tables — per-user persistent column visibility
- **Tables**: Server-side sortable column headers across all major admin tables
- **AI**: Intelligence engine for automated performance scoring and agent coaching
- **CRM Campaigns**: WhatsApp campaign support

### Changed
- Performance dashboard templates polished to full Industrial Modern design system spec

### Security
- Additional hardening across authentication and data access paths

---

## [2026-02-13]

### Added
- **Vendor Portal**: UX improvements for quotes, as-built reports, and project views
- **Vendor Quotes**: Lifecycle events (submitted → approved → rejected → work order created)
- **ERP Integration**: Auto-create purchase order on DotMac ERP when work order is created from an approved vendor quote

### Fixed
- Audit log actor resolution corrected for system-generated events
- Dashboard activity names standardised
- Admin sidebar section ordering reorganised
- CRM inbox authentication enforcement tightened
- Inbox outbox race conditions resolved
- Email connection leaks plugged
- Webhook response status codes corrected; signature enforcement applied
- Meta (WhatsApp/Instagram) inbound notification routing fixed
- Mention email HTML escaping fixed
- Admin filter state for tickets and projects now persists correctly
- Sales metrics, lead close state, vendor access controls, and ERP stats cleaned up
- `QuoteLineItemUpdate` import restored in vendor service
- Projects `per_page` filter no longer reset on page navigation
- Vendor portal login allowed for accounts without active subscription

### Security
- Rate limiting exemption applied to `/webhooks/*` endpoints
- pytest config secured; ERP sync field access controlled
- PII-sensitive paths added to `.gitignore`
- Inbox rate limiting applied to outbound message paths

---

## [2026-02-12]

### Added
- **ERP**: ERPNext resync support with `erpnext_id` tracking on all synced entities
- **ERP**: Comment imports from ERPNext into CRM

---

## [2026-02-10]

### Added
- **Workforce**: `ServiceTeam` model for grouping technicians into dispatch teams
- **Inventory**: `MaterialRequest` model for field material requisitions
- **ERP**: Full bidirectional ERP sync for workforce and inventory entities
- **CRM Inbox**: Integration with workforce module (work order creation from conversations)
- Comprehensive test coverage for all new models and sync flows

---

## [2026-02-08]

### Added
- **Branding**: Full white-label support across all portals (admin, customer, reseller, vendor), all auth pages, and all email templates — driven by `DomainSetting` table
- **CRM Inbox**: Outbox/retry system — failed outbound messages queued for automatic retry with exponential backoff
- **CRM Inbox**: Inbox metrics dashboard (conversation volume, resolution time, agent performance)
- **UI**: `typeahead_input` macro added to design system; migrated all 14 existing typeahead instances
- **UI**: Design system standardisation pass — all inbox views updated to Industrial Modern spec

### Changed
- Inbox web routes refactored — business logic moved to service layer

---

## [2026-02-04]

### Added
- **Tickets**: Pre-creation validation — checks for duplicate open tickets before creating new ones
- **Projects**: `effort_hours` field on template tasks for effort estimation

### Fixed
- Widget prechat form CORS error handling corrected
- Dead quote PDF links removed from widget

---

## [2026-02-03]

### Added
- **CRM**: Module split into logical subdomains — contacts, inbox, sales, teams, campaigns — with dedicated router and service files per subdomain
- **CRM Campaigns**: Multi-step drip campaign builder with WhatsApp, SMS, and email channels; scheduling, audience filters, and step management
- **ERP**: ERPNext integration scaffolding — bidirectional sync for contacts, leads, and quotes
- **Automation**: Database-configurable automation rules engine — trigger/condition/action model, no code changes required for new rules
- **Surveys**: Customer survey tool with NPS, star-rating, and custom question types; multi-channel distribution (email, SMS, WhatsApp, widget)

### Removed
- Legacy subscription management code cleanup (billing, catalog, NAS/RADIUS remnants)

---

## [2026-01-xx]

### Changed
- Initial commit of DotMac CRM (rebranded from dotmac_omni)
- Removed customer and reseller portals from initial public release scope
