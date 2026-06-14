# Findings — Portals (Customer / Vendor / Reseller)

Tested unauthenticated entry to each portal from `http://localhost:8010`.

---

## BUG-050 — Login page "Customer" portal link is broken (`/portal` → raw JSON 404)

**Severity:** BUG (high — advertised entry point is dead)

**Where:** `templates/auth/login.html:246`

```html
<a href="/portal" ...>...<span>Customer</span></a>
```

**Symptom:** The admin login page shows an "Access other portals" section with
**Customer** and **Vendor** tiles. Clicking **Customer** navigates to `/portal`,
which returns a raw JSON body:

```json
{"detail":"Not Found"}
```

No customer-portal route is mounted under `/portal` in `app/web/`. So the link is
either pointing at the wrong path or the portal isn't wired up.

**Impact:** Customers (and anyone following the advertised link) hit a raw JSON
404 instead of a login page.

**Suggested fix:** Point the link at the real customer-portal login path (the
Vendor tile correctly uses `/vendor`, which redirects to `/vendor/auth/login`),
or remove the tile until the portal exists.

---

## BUG-051 — `/reseller/dashboard` returns a raw JSON 403 instead of a login redirect / styled page

**Severity:** BUG (medium — UX, inconsistent auth handling)

**Symptom:** Hitting `/reseller` (unauthenticated) redirects to
`/reseller/dashboard`, which renders a raw JSON error in the browser:

```json
{"code":"http_403","message":"Reseller access required","details":null}
```

Security is correct (access is denied), but the user sees raw JSON. Compare the
**Vendor** portal, which cleanly redirects an unauthenticated visitor to
`/vendor/auth/login`.

**Impact:** Confusing dead-end for reseller users; inconsistent with the vendor
portal's behavior.

**Suggested fix:** For browser (HTML-accepting) requests, redirect unauthenticated
reseller traffic to the reseller login page, or render a styled 403/login page;
reserve JSON error bodies for API clients.

---

## POSITIVE — Vendor portal handles unauthenticated entry correctly

`/vendor` redirects to `/vendor/auth/login` and renders a proper styled
"Vendor Sign In" page (title: *Vendor Sign In - Dotmac CRM*).

---

## NOTE-052 — Raw JSON error bodies for browser navigations is a recurring pattern

Both `/portal` (404) and `/reseller/dashboard` (403) return raw JSON to a browser
navigation. Consider a global exception handler that content-negotiates: HTML
error page for `Accept: text/html`, JSON for API clients. This also covers
`/admin/reports` (404) and similar bare index routes.
