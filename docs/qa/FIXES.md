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

## Notes / not changed

- **BUG-011 (pagination):** the contacts/leads pagers already apply
  `opacity-50 pointer-events-none` when `page <= 1` / `page >= total_pages`, so the
  Prev/Next controls are visually disabled on a single page. The original finding
  was based on the accessibility tree still listing `<a href>` (which CSS
  `pointer-events:none` does not remove). **Re-classified as low-priority polish**
  — recommend dropping the `href` (or `href="#"`) when on a boundary so the link is
  truly inert; no functional bug. No code change made.
- **Follow-up (post-login redirect):** portal-only users still get redirected to
  `/admin/dashboard` after login, which now correctly 403s. A nicer UX would route
  reseller/vendor principals to their own portal. Out of scope for these fixes.
- **BUG-131 deeper fix:** the report still makes per-row external Splynx calls
  inside the request; the proper long-term fix is a background job + cache. The
  patch here only prevents the 500 (graceful degradation).
- **BUG-151 (contact `.local` email rejection):** not changed — needs a product
  decision on whether to relax email validation; the raw-error-message UX could be
  improved separately.
