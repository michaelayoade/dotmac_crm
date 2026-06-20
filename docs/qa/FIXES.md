# QA Fixes — branch `qa/exploratory-qa-fixes`

Fixes for findings in [SUMMARY.md](SUMMARY.md), verified live against the seeded
`crm_test` app on :8010 (Playwright + authenticated fetch sweeps).

## Changes

| Bug | Sev | Fix | Files |
|-----|-----|-----|-------|
| **BUG-140** | Critical | Router-level admin guard: a valid web session is no longer sufficient — the principal must hold a staff role (`admin/operator/support/auditor/field_technician`) **or** ≥1 effective permission. Closes the ~93 ungated admin pages in one place. | `app/web/admin/__init__.py` |
| **BUG-130** | High | `risk_alerts.items` → `risk_alerts['items']` (Jinja was iterating the dict `.items` method). | `templates/admin/intelligence/readiness.html` |
| **BUG-131** | High | `_fallback_service_price` tolerates Splynx failures (returns 0); revenue-service route catches any build error and renders a friendly banner instead of 500. | `app/services/revenue_service_report.py`, `app/web/admin/reports.py` |
| **BUG-040** | High | Moved shared layout JS (`locationShareToggle`, voice input, confirm modal) from the overridable `{% block scripts %}` into a new non-overridable `{% block layout_scripts %}` rendered by `base.html`. Page templates overriding `scripts` can no longer drop it. | `templates/base.html`, `templates/layouts/admin.html` |
| **BUG-150** | Med-High | Edit form fields use `(value or '')` so null description/sku/unit no longer pre-fill the literal `"None"` (which previously persisted on save). | `templates/admin/inventory/item_form.html` |
| **BUG-080** | Low-Med | Audit filter inputs use `default('', true)` so Python `None` renders empty, not `"None"`. | `templates/admin/system/audit.html` |
| **BUG-101** | Low | Projects list subtitle uses `default(..., true)` so a null code renders the fallback, not `"None"`. | `templates/admin/projects/index.html` |
| **BUG-010** | High | Leads sidebar link `https://crm.dotmac.io/admin/crm/leads` → relative `/admin/crm/leads`. | `templates/components/navigation/admin_sidebar.html` |

## Live verification results

- **BUG-140:** as reseller (`reseller_admin`, 0 permissions) all 30 previously-leaking
  pages now return **403** (incl. `system/api-keys`, `subscribers`,
  `integrations/erpnext`, `dashboard`, `crm/contacts`). As `qaadmin` (admin) all 10
  spot-checked pages still return **200** — no regression.
- **BUG-130 / BUG-131:** `/admin/intelligence/readiness` and
  `/admin/reports/revenue-service` now return **200** (were 500).
- **BUG-040:** `/admin/crm/sales` and `/admin/system/permissions` now report **0**
  console errors (were 6 each); confirmed across template families.
- **BUG-080:** audit filter inputs render `""` (were `"None"`).
- **BUG-101:** project subtitles render `PROJ-12…` (were `"None"`).
- **BUG-010:** sidebar Leads `href="/admin/crm/leads"`; no `crm.dotmac.io` anywhere in the sidebar.
- **BUG-150:** inventory edit form Description/Unit render empty for a null-description item (were `"None"`).

## Round 2 (commits 3b842a2, ac3f54c, 21a7488)

| Bug | Sev | Fix | Files |
|-----|-----|-----|-------|
| **BUG-002** | High | Fresh-DB startup crash: `seed_workflow_settings` passed `value_text=None` for an integer setting; default to `""` and harden `ensure_by_key` to coerce `None`→`""` for non-json settings. | `app/services/settings_seed.py`, `app/services/domain_settings.py` |
| **BUG-071** | Med | Inventory create/update reject duplicate SKUs among active items (verified: dup `FIB-SC-001` → 400, no record). | `app/services/inventory.py` |
| **BUG-030** | Med-High | Leads "Pipeline Value" now excludes won/lost (open opportunities only) → ₦15,000 not 28,000, matching the dashboard. | `app/services/crm/web_leads.py` |
| **BUG-031** | Low-Med | Leads pipeline value renders the ₦ symbol (consistent with dashboard). | `templates/admin/crm/leads.html` |
| **BUG-090** | Med | Sidebar Workqueue link gated on real `workqueue:view` permission (dropped `is_admin` bypass) so it never 403s for users who see it. | `templates/components/navigation/admin_sidebar.html` |
| **BUG-050** | High | Removed dead "Customer" portal tile from login (`/portal` not mounted). | `templates/auth/login.html` |
| **BUG-011** | Low | Pagination Prev/Next drop their `href` (and add `aria-disabled`) on boundary. | `templates/admin/crm/{contacts,leads}.html` |
| **BUG-151** | Low-Med | Contact create/update validation errors are field-scoped and no longer leak the pydantic schema class name. | `app/web/admin/crm_contacts.py` |
| **NOTE-042** | — | `/favicon.ico` → 308 redirect to `/static/favicon.svg`. | `app/main.py` |
| **BUG-051/090/052/132** | Med | Error handlers content-negotiate: browser navigations get styled 403/404/500 pages; API/HTMX keep JSON. Registered for Starlette's `HTTPException` too (covers unmatched-route 404s like `/portal`). | `app/errors.py`, `templates/errors/403.html` |
| **NOTE-110** | — | Index redirects for `/admin/network`, `/admin/integrations`, `/admin/intelligence`, `/admin/reports`. | respective routers |
| **BUG-070** | Med | Material-request "Add Item" uses a name+SKU select instead of a raw inventory UUID. | `app/web/admin/material_requests.py`, `templates/admin/material_requests/detail.html` |
| **BUG-131 (deeper)** | High | 5-minute TTL cache around `build_report` (first load ~42s → cached ~50ms), plus the earlier graceful-degradation handler. | `app/services/revenue_service_report.py` |

### Round 2 live verification
- BUG-002: `ensure_by_key` None→"" coercion (unit-correct); app boots.
- BUG-071: dup SKU create → "400: An active inventory item with SKU 'FIB-SC-001' already exists.", no record created.
- BUG-030/031: leads page shows **₦15,000** (was `28,000`).
- BUG-090: Workqueue link absent for qaadmin (lacks `workqueue:view`).
- BUG-050: no `/portal` link on login.
- BUG-151: error reads "Email: value is not a valid email address…" (no `ContactCreate`).
- NOTE-042: `/favicon.ico` → 308 → `/static/favicon.svg`.
- BUG-051/052/132: `/portal` → styled HTML 404 for browsers, JSON for API; reseller hitting `/admin/*` → styled **403** page (screenshot `26-styled-403.png`).
- BUG-070: Add-Item shows a 12-option name+SKU `<select>`, no UUID input.
- BUG-131: revenue-service 200; first build 42.7s, cached 50ms.

### BUG-001 — attempted, reverted (documented, not shipped)
The squashed initial migration is broken for fresh installs in **multiple** ways:
(a) forward-reference FKs (e.g. `installation_projects`→`vendors`) — fixable by
deferring all FKs (a verified transform did this), **but** (b) the chain also
re-creates tables that later revisions create (e.g. `document_sequences` is created
by both the initial schema *and* migration `d1e2f3a4b5c6`), so `alembic upgrade head`
on an empty DB still fails after the FK fix. A safe fix requires regenerating a
single clean baseline migration from the current models (and stamping existing
DBs) — out of scope for a low-risk patch. The migration edits were **reverted**;
the QA harness's schema-clone approach remains the fresh-DB workaround.

## Notes / not changed

- **BUG-011 (pagination):** now fixed in Round 2 — Prev/Next drop their `href` and
  gain `aria-disabled` on a boundary (the pagers were already visually disabled via
  `pointer-events:none`; this makes them truly inert).
- **BUG-120 (search echo):** not a bug — the contacts search input already binds
  `value="{{ search }}"` and `search-autofill-guard.js` explicitly preserves
  URL-param searches. The original empty-value observation was an artifact of the
  XSS-payload test. No change.
- **Follow-up (post-login redirect):** portal-only users still get redirected to
  `/admin/dashboard` after login, which now correctly 403s. A nicer UX would route
  reseller/vendor principals to their own portal. Out of scope for these fixes.
- **BUG-131 deeper fix:** the report still makes per-row external Splynx calls
  inside the request; the proper long-term fix is a background job + cache. The
  patch here only prevents the 500 (graceful degradation).
- **BUG-151 (contact `.local` email rejection):** not changed — needs a product
  decision on whether to relax email validation; the raw-error-message UX could be
  improved separately.
