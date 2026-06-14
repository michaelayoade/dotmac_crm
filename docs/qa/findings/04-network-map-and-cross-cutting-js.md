# Findings — Network Map & cross-cutting JS/template defect

Screenshot: `../screenshots/06-network.png`

---

## BUG-040 — Pages overriding `{% block scripts %}` without `{{ super() }}` break header Alpine widgets

**Severity:** BUG (high — JS errors on ~12 admin pages, broken header control)

**Where (root cause):**
- `templates/layouts/admin.html` defines the header location-share Alpine
  component inside `{% block scripts %}` (block starts line 707;
  `function locationShareToggle()` at line 1159). The header markup at
  `admin.html:361` uses `x-data="locationShareToggle()" x-init="init()"`.
- Child templates that override `{% block scripts %}` **without calling
  `{{ super() }}`** drop that definition, so Alpine evaluates the header's
  `locationShareToggle()` / `init()` / `isSharing` / `loading` against an
  undefined component.

**Symptom (observed on `/admin/network/map`, "Fiber Plant Map"):** 6 console errors:

```
ReferenceError: locationShareToggle is not defined   (x-data, header)
ReferenceError: init is not defined                  (x-init, header)
ReferenceError: loading is not defined
ReferenceError: isSharing is not defined   (x3 — label + classes on the toggle)
```

The "Start/Stop sharing location" header button is non-functional on these pages.

**Scope — child templates of the admin layout that override `scripts` without
`super()` (each affected):**

```
admin/customers/detail.html
admin/network/fiber/map.html
admin/network/fiber/fdh-cabinet-detail.html
admin/network/fiber/splice-closure-detail.html
admin/network/fiber/reports.html
admin/network/qa/remediations.html
admin/system/roles_form.html
admin/system/permissions.html
admin/system/permissions_form.html
admin/crm/sales_pipeline.html
admin/crm/sales_dashboard.html
admin/crm/quote_detail.html
admin/crm/lead_form.html
admin/vendors/quotes/route-view.html  (verify against its layout)
```

(17 templates override `block scripts` without `super()` overall; `base.html`,
`layouts/admin.html`, and the vendor-layout templates are structural/expected.)

**Suggested fix:** Either (a) add `{{ super() }}` at the top of each child
`{% block scripts %}`, or (b) move shared header components like
`locationShareToggle()` out of the overridable `scripts` block into a
non-overridable block (e.g. a dedicated `{% block layout_scripts %}` rendered
unconditionally by the layout). Option (b) is the robust fix — it prevents the
class of regression rather than fixing each page.

**Confirmed live in-browser (6 console errors each):** `/admin/network/map`,
`/admin/crm/sales`, `/admin/crm/leads/new`, `/admin/system/permissions`,
`/admin/system/roles/new`. A ~30-page console sweep across all template families
found **no JS error signatures other than this (BUG-040) and BUG-100** — every
client-side error converges on these two root causes.

---

## NOTE-041 — `/admin/network` and `/admin/network/olts` have no page (404)

There is no network index route; real pages live under `/admin/network/map`,
`/fiber-plant` (→ redirects to `/map`), `/pop-sites`, `/fdh-cabinets`,
`/fiber-change-requests`, etc. Navigating to `/admin/network` returns a 404.
Confirm the sidebar never links to a bare `/admin/network`.

---

## NOTE-042 — `favicon.ico` 404 app-wide

The app ships `static/favicon.svg` but browsers also request `/favicon.ico`,
which 404s on every page (harmless console noise). Add an `.ico` or a
`<link rel="icon">` / route alias.
