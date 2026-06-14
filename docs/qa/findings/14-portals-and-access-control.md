# Findings — Portals (Vendor, Reseller) & Broken Access Control

Seeded portal users (`scripts/seed_portal_users.py`) and drove both portals:
- Vendor: `vendoruser@test.local` / `VendorQA!2026Secure#Pass` (via `/vendor/auth/login`)
- Reseller: `reselleruser@test.local` / `ResellerQA!2026Secure#Pass` (via `/auth/login`)

Screenshots: `21-vendor-dashboard.png`, `22-vendor-projects.png`,
`23-reseller-sees-admin-contacts.png`, `24-reseller-dashboard.png`.

---

## BUG-140 — Broken access control: reseller-only user can reach **93 of 160 admin pages** (58%)

**Severity:** BUG (**CRITICAL** — OWASP A01 Broken Access Control / mass data exposure)

**Setup:** `reselleruser@test.local` has only the `reseller_admin` role and belongs
to the "QA Reseller Networks" org — no admin/operator role or permissions.

**Full authenticated sweep (all 160 navigable admin pages, as the reseller):**
**93 returned `200` (accessible) / 67 returned `403` (gated).** Full data:
`../reseller-access-FULL.json`. Confirmed rendering real data in-browser:
`/admin/crm/contacts` shows the full 15-row system contact list incl.
"Ada Lovelace" (screenshot `23-...`); `/admin/system/api-keys` renders
(screenshot `25-...`).

**Most sensitive leaked pages (accessible to a reseller — should be admin-only):**

| Category | Leaked pages (sample) |
|----------|------------------------|
| **Credentials/secrets** | `/admin/system/api-keys`, `/admin/system/api-keys/new`, `/admin/integrations/{connectors,erpnext,webhooks,providers,channels}` (+ `/new`) |
| **Customer PII / financial** | `/admin/subscribers` (+`/new`,`/billing-risk`,`/resellers`), `/admin/reports/subscribers/{revenue,billing-risk,churned,lifecycle,overview,service-quality}` |
| **CRM data** | `/admin/crm/{contacts,inbox,quotes,sales,widget}` (+ create/edit forms) |
| **Ops / network** | all `/admin/operations/*` (work-orders, dispatch, material-requests, sales-orders, technicians), all `/admin/network/*`, `/admin/inventory*` |
| **System** | `/admin/system/{admin-hub,health,teams,legal,users/profile}`, `/admin/dashboard`, `/admin/vendors*`, `/admin/surveys*` |

Many leaked routes are `/new`/`/edit` **create/update forms**, so this likely
extends to write access, not just read. Correctly gated (403): `system/users`,
`system/roles`, `system/permissions`, `system/audit`, `system/settings`,
`system/webhooks`, `system/scheduler`, `support/tickets*`, `projects*`,
`crm/leads*`, `crm/campaigns`, `data-quality`, `intelligence/insights`,
`performance/team`, `reports/ncc`, `vendors/{quotes,purchase-invoices}`.

**Scale:** this is not 2 stray pages — it is **systemic**: ~58% of the admin
surface lacks a permission guard. Any authenticated principal (a reseller, and
by extension a vendor-linked person or any low-privilege account that can obtain
a web session) can read — and likely write — across nearly the whole admin app.

**Root cause:** inconsistent route guards.
- `app/web/admin/dashboard.py:13` — `def dashboard(request, db=Depends(get_db))` — **no** `require_permission`.
- `app/web/admin/crm_contacts.py:115` — `crm_contacts_list(...)` — **no** `require_permission`.
- Contrast `app/web/admin/system.py:1262` — `/users` has
  `dependencies=[Depends(require_permission("rbac:roles:read"))]` → correctly 403.

So some admin routes are permission-gated and others are not; the ungated ones
are reachable by any authenticated principal, including portal-only users.

**Suggested fix:** Add an appropriate `require_permission(...)` (e.g.
`crm:contacts:read`, `dashboard:read`) to the admin dashboard and CRM contacts
routes — and audit *all* admin routes for a consistent guard (a router-level
dependency on the admin router would prevent this class of gap). Also tenant-scope
contact queries so even authorized non-admins can't read other orgs' data.

---

## BUG-141 — Vendor builder pages return raw 422 JSON on direct navigation

**Severity:** BUG (medium — UX)

`/vendor/quotes/builder`, `/vendor/invoices/builder`, and `/vendor/as-built/submit`
return **HTTP 422 `application/json`** when opened without their required query
params (e.g. `project_id`). A vendor who bookmarks or deep-links these (or follows
a stale link) gets a raw JSON error page. Provide a friendly empty/selector page
or redirect when the param is absent.

---

## BUG-050 (confirmed) — No customer portal exists

The admin login page advertises a **Customer** portal tile linking to `/portal`,
but **no `/portal` router is mounted** anywhere in `app/web/` — only Vendor and
Reseller portals exist. `/portal` returns raw JSON `{"detail":"Not Found"}`.
Either build/mount the customer portal or remove the tile.

---

## POSITIVE — Vendor & Reseller portals work and are clean

- **Vendor login** works → `/vendor/dashboard`. `/vendor/dashboard`,
  `/vendor/projects/available`, `/vendor/projects/mine`, `/vendor/fiber-map` all
  render `200` with **0 console errors** (vendor uses its own layout — not
  affected by BUG-040).
- **Reseller login** works (main `/auth/login`). `/reseller/dashboard`,
  `/reseller/subscribers`, `/reseller/contacts`, `/reseller/accounts`,
  `/reseller/fiber-map` all render `200`, dashboard has **0 console errors**.
- Reseller access control on its *own* surface is correct (the 403s above show the
  reseller RBAC is enforced where guards exist).
