# Findings — Exhaustive route sweep (all admin pages)

Method: extracted **all** `@router.get` routes from `app/web/admin/*.py` (324
total), filtered to real navigable pages (excluding partials, exports, JSON/api,
and GET action endpoints), substituted seeded IDs for path params, then issued an
authenticated `fetch()` to every URL from the logged-in browser context and
classified each by HTTP status + `Content-Type`.

**Result across 157 real admin pages:** 155 return `200 text/html`; **2 return
`500`**; 0 unexpected 404s; 0 raw-JSON on real pages (the few JSON 200s are
data-feed endpoints — `agents/presence/live-map`, `inbox/whatsapp-contacts` —
which correctly return JSON).

Raw data: `../route-sweep.json`. Screenshot: `../screenshots/20-readiness-500.png`.

---

## BUG-130 — `/admin/intelligence/readiness` returns 500 (Jinja `.items` method-iteration)

**Severity:** BUG (high — page completely broken)

**Where:** `templates/admin/intelligence/readiness.html:49`

```jinja
{% for alert in risk_alerts.items %}
```

**Root cause:** `compute_effective_risk_alerts(...)` returns a **dict**
(`app/services/ai/data_health.py:462`, `-> dict[str, Any]`) whose alert list is
under a key literally named **`items`**. In Jinja, `risk_alerts.items` resolves
to the dict's built-in **`.items` method**, not the `"items"` key — so
`{% for alert in <method> %}` raises:

```
TypeError: 'builtin_function_or_method' object is not iterable
```

(Traceback: `intelligence.py:336` → template render.) The page shows a raw JSON
500 to the user: `{"code":"internal_error","message":"Internal server error"}`.

**Trigger condition:** only fires when `risk_alerts.has_alerts` is truthy (the
`{% if %}` on line 45 guards the block) — i.e. when there *are* alerts, the page
crashes. With no alerts it renders.

**Suggested fix:** use subscript access for the key: `{% for alert in risk_alerts['items'] %}`
(or rename the dict key away from the reserved `items`/`keys`/`values` names).

---

## BUG-131 — `/admin/reports/revenue-service` returns 500 (external API call during render, inside open DB transaction)

**Severity:** BUG (high — page broken; architectural smell)

**Where:** `app/web/admin/reports.py:3073` → `revenue_service_report.build_report`
→ `_fallback_service_price` → `app/services/splynx.py:685
fetch_customer_internet_services`.

**Root cause:** Rendering the report makes **live external Splynx API calls**
(`https://selfcare.dotmac.ng/...`) synchronously during the request. In the test
env Splynx returns `403 Forbidden`; more importantly the request holds a DB
transaction open across the slow external call, so Postgres kills it:

```
sqlalchemy.exc.OperationalError: terminating connection due to
idle-in-transaction timeout
```

→ HTTP 500.

**Impact:** The revenue-service report is unusable whenever Splynx is slow,
unreachable, or unauthenticated, and risks exhausting DB connections via
idle-in-transaction. This is a resilience problem beyond the test environment.

**Suggested fix:** Don't call external services inside the request/transaction —
precompute via a Celery job + cache, or at minimum commit/close the DB
transaction before the external call and wrap the call in a timeout + graceful
fallback (the page already has a `_fallback_service_price` path; it should
tolerate Splynx failure instead of propagating).

---

## NOTE-132 — 500 errors render as raw JSON to the browser

Both 500s return `{"code":"internal_error",...}` as `application/json` to a normal
browser navigation (no styled 500 page). Same content-negotiation gap as
BUG-051 / BUG-090 / NOTE-052 — a global HTML error page for `Accept: text/html`
would cover 403/404/500 uniformly.

---

## Coverage note

155/157 navigable admin GET pages return healthy HTML (verified by authenticated
status + content-type sweep). The earlier "404s" in the first sweep pass were
path-construction errors on my side (CRM sub-routers live under `/admin/crm/...`;
`admin-hub`/`legal` under `/admin/system/...`) — re-tested with correct prefixes,
all return 200. So aside from BUG-130/131 the admin surface loads cleanly at the
HTTP level. (HTTP-level only; per-page console/JS issues like BUG-040 are tracked
separately.)
