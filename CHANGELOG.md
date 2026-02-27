# Changelog

All notable changes to DotMac CRM are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

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
