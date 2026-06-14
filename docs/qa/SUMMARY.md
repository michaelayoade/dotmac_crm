# QA Exploratory Pass — Summary

Environment: seeded `crm_test` DB + `dotmac_omni_app_test` on **http://localhost:8010**.
Driver: Playwright MCP (live browser), logged in as `qaadmin`.
Date: 2026-06-14.

See [README.md](README.md) for how the environment was built and
[findings/](findings/) for full per-area detail.

## Findings register

| ID | Sev | Area | Summary |
|----|-----|------|---------|
| BUG-001 | High | Migrations | Fresh DB can't build — FK ordering in squashed initial migration (`installation_projects`→`vendors`) |
| BUG-002 | High | Startup | App crashes on empty DB — `seed_workflow_settings` builds invalid `DomainSettingCreate` (missing `value_text`) |
| BUG-010 | High | Nav | "Leads" sidebar link hardcoded to `https://crm.dotmac.io/...` (off-environment nav) |
| BUG-011 | Med | Pagination | Shared pager shows "Page 1 of 1" yet renders active Prev (`page=0`)/Next (`page=2`) — Contacts + Leads |
| BUG-030 | Med-High | CRM Leads | "Pipeline Value" sums **all** leads incl. Lost & Won (28,000 vs dashboard's correct 15,000) |
| BUG-031 | Low-Med | CRM | Currency formatted 3 ways for same data (`USD 7,000.00` / `28,000` / `₦15,000`) |
| BUG-040 | High | Templates/JS | ~12 admin pages override `{% block scripts %}` without `{{ super() }}`, dropping `locationShareToggle()` → Alpine `ReferenceError`s, broken header control |
| BUG-050 | High | Portals | Login page "Customer" tile → `/portal` returns raw JSON `{"detail":"Not Found"}` |
| BUG-051 | Med | Portals | `/reseller/dashboard` shows raw JSON 403 instead of login redirect/styled page |
| BUG-070 | Med | Materials | MR "Add Item" requires pasting a raw inventory UUID (no item picker) |
| BUG-071 | Med | Inventory | `inventory_items.sku` has no unique constraint — duplicate SKUs allowed |
| BUG-080 | Low-Med | Audit | Audit Log filter inputs render literal "None" (`default('')` doesn't catch Python `None`) |
| BUG-090 | Med | Nav | Admin sidebar "Workqueue" → `/agent/workqueue` returns raw JSON 403 |
| BUG-100 | Med | Subscribers | List throws `allSelected is not defined` — select-all rendered outside bulk `x-data` scope |
| BUG-101 | Low | Projects | List renders literal "None" subtitle (None-leak, see BUG-080) |
| BUG-130 | High | Intelligence | `/admin/intelligence/readiness` 500 — Jinja `risk_alerts.items` iterates the dict method (readiness.html:49) |
| BUG-131 | High | Reports | `/admin/reports/revenue-service` 500 — live external Splynx call during render → idle-in-transaction timeout |
| BUG-140 | **CRITICAL** | Access control | Reseller-only user reaches **93 of 160 admin pages (58%)** incl. `system/api-keys`, `subscribers` PII, all `integrations/*` + create forms — systemic missing `require_permission`. Data: `reseller-access-FULL.json` |
| BUG-141 | Med | Vendor portal | `/vendor/{quotes,invoices}/builder`, `/as-built/submit` return raw 422 JSON without required params |
| NOTE-110 | — | Routing | Many sections 404 on bare index (`/integrations`, `/service-teams`, `/reports`, `/network`, `/intelligence`, `/automations`, `/material-requests`) |
| NOTE-120 | — | Search | Contacts search term not echoed back into the search input |
| BUG-150 | Med-High | Inventory | Edit form persists literal "None" into Description (None-leak in editable field → data corruption) |
| BUG-151 | Low-Med | CRM | Contact create rejects `.local` emails with a raw pydantic error leaking schema name `ContactCreate` |
| NOTE-003 | — | Data | `people.email` is `NOT NULL` (no email-less contacts) |
| NOTE-012 | — | Dashboard | Pipeline value symbol hardcoded ₦ regardless of lead currency |
| NOTE-013 | — | Contacts | Whitespace-padded names trimmed on display |
| NOTE-020 | — | Tickets | Default list hides terminal-status tickets (20 of 26) — confirm intended |
| NOTE-041 | — | Network | No `/admin/network` index (404); real pages under sub-paths |
| NOTE-042 | — | Global | `favicon.ico` 404 app-wide (only `favicon.svg` shipped) |
| NOTE-052 | — | Global | Raw JSON error bodies returned to browser navigations (no HTML/JSON content negotiation) |

Positives observed: HTML output is correctly escaped — no reflected XSS (search)
and no stored XSS (ticket title `<img onerror>` payload rendered inert); invalid
& malformed UUIDs return clean 404s (no 500); ticket create + inline status
update work with PRG + DB persistence + audit trail; material-request Draft→
Submitted workflow works; required-field validation blocks empty submits; ticket
status/priority badges render across all enums; vendor portal auth redirect is
clean; RBAC user list correct.

## Coverage matrix

| Area | Path | Depth | Result |
|------|------|-------|--------|
| Dashboard | `/admin/dashboard` | Deep | ✅ renders; currency note |
| CRM Contacts | `/admin/crm/contacts` | Deep + edge | ✅ escaping good; pager + currency bugs |
| CRM Leads | `/admin/crm/leads` | Deep + edge | ⚠️ pipeline-value & currency bugs |
| Support Tickets | `/admin/support/tickets` | Deep | ✅ healthy |
| System Users (RBAC) | `/admin/system/users` | Deep | ✅ healthy |
| Network / Fiber Map | `/admin/network/map` | Deep | ⚠️ Alpine JS errors (BUG-040) |
| CRM Inbox | `/admin/crm/inbox` | Smoke | ✅ loads, 0 errors |
| Subscribers | `/admin/subscribers` | Smoke | ✅ loads |
| Operations / Work Orders | `/admin/operations/work-orders` | Smoke | ✅ loads |
| CRM Quotes + New Quote | `/admin/crm/quotes` | Deep | ✅ clean empty state |
| Sales Dashboard | `/admin/crm/sales` | Deep | ⚠️ BUG-040 Alpine errors |
| New Lead form | `/admin/crm/leads/new` | Deep + edge | ⚠️ BUG-040; ✅ required-field validation |
| Material Requests + detail | `/admin/operations/material-requests` | Deep + workflow | ✅ Draft→Submitted works; ⚠️ UUID add-item |
| Inventory | `/admin/inventory` | Deep | ⚠️ duplicate SKUs (BUG-071) |
| System Audit | `/admin/system/audit` | Deep | ⚠️ "None" filter values (BUG-080); ✅ trail works |
| Campaigns | `/admin/crm/campaigns` | Smoke | ✅ loads |
| Automations | `/admin/system/automations` | Smoke | ✅ loads |
| Projects | `/admin/projects` | Smoke | ✅ loads |
| Vendor portal | `/vendor` | Smoke | ✅ clean login redirect |
| Reseller portal | `/reseller` | Smoke | ⚠️ raw JSON 403 (BUG-051) |
| Customer portal | `/portal` | Smoke | ❌ raw JSON 404 (BUG-050) |
| Edge: invalid/malformed IDs | `/admin/crm/contacts/<bad>` | Edge | ✅ clean 404 |

## Exhaustive coverage achieved (admin surface)

- **HTTP sweep:** all 324 admin GET routes enumerated from source; 157 real
  navigable pages fetched authenticated. **155/157 → 200 HTML**, 2 → 500
  (BUG-130, BUG-131). No unexpected 404s; JSON-200 responses are data feeds.
  Raw data: `route-sweep.json`. Detail: `findings/13-exhaustive-route-sweep.md`.
- **Console/JS sweep:** ~30 pages across every template family. All client-side
  errors converge on **BUG-040** (`block scripts` override) and **BUG-100**
  (`allSelected`) — no other signatures.
- **Workflows exercised:** ticket create + inline status change (DB-persisted +
  audited), material-request Draft→Submitted, required-field validation,
  reflected + stored XSS (both safe).

## Portals covered (authenticated)

Seeded vendor + reseller portal users (`scripts/seed_portal_users.py`) and drove
both with Playwright:
- **Vendor** portal (`/vendor/*`): login + dashboard/projects/fiber-map all clean.
- **Reseller** portal (`/reseller/*`): login + dashboard/subscribers/contacts/
  accounts/fiber-map all clean.
- **Access-control probe** surfaced **BUG-140** (reseller reads admin dashboard +
  CRM contacts). No customer portal exists (BUG-050). Detail:
  `findings/14-portals-and-access-control.md`.

## Not yet covered (needs extra setup / out of scope)

- **Public routes** (`/s/<survey>`, `/legal/<public>`, inbound webhooks).
- **Mutations not run** to avoid destructive changes: bulk delete/assign, CSV
  export contents, file-upload validation, ticket merge with invalid IDs.
- **AI features** requiring live model API keys (insights generation).
