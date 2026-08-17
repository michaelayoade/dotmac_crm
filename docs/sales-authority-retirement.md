# CRM sales-authority retirement

**Status:** Retirement inventory complete; implementation blocked by Starter
P11 and Sub module adoption
**As of:** 2026-08-17
**CRM evidence pin:** `57e112f0757edcee6b9ad625ee3e13ebff5c7d71`
**Sub authority/source pin:** `f64946fc451ba94a1d4c8f0a61b7831367d5b598`
**Starter decision pin:** `7828697ef11fb1ae765a5397dfa7dc221ae6207a`
**ERP requirements pin:** `2749ec5396cbbd7a1132b394e85855a1d133a7cd`
**Current sales system of record:** Sub
**Target reusable owner:** Starter `dotmac-sales`, through accepted Quote only

## Authority correction

CRM is not the source of truth for customer-sales Leads, Pipelines,
opportunity Stages or Quotes. Sub's approved
`docs/designs/SALES_TO_SERVICE_LIFECYCLE_SOT.md` is the current authority and
the product-first implementation source. CRM's local sales implementation is
parity, migration and writer-retirement evidence.

The reusable boundary stops at an accepted, immutable Quote and a versioned,
product-neutral handoff. It does not include SalesOrder rows and does not import
`dotmac-orders`. Orders, customer-account conversion, projects, work orders,
billing, provisioning and service activation remain separate downstream
owners.

This ruling does not assign campaign/audience ownership, change Inbox or
conversation ownership, change WhatsApp/connector/consent ownership, or decide
customer-retention case management. CRM's campaign rows remain unverified
pending a separate source audit. Retention guidance conflicts and needs an
explicit owner decision.

## As-built CRM sales surface

CRM still carries five local tables:

- `crm_pipelines`;
- `crm_pipeline_stages`;
- `crm_leads`;
- `crm_quotes`; and
- `crm_quote_line_items`.

Their existence and active writers are migration facts, not authority.

### Primary writers and routes

| Path | Surface | Retirement treatment |
| --- | --- | --- |
| `app/services/crm/sales/service.py` | constructors and CRUD/search/Kanban/status logic for all five rows | freeze after backfill; re-point commands/reads; delete local writer after cutover |
| `app/api/crm/sales.py` | 21 Pipeline/Stage/Lead/Quote/line routes | migrate every caller; retire after production zero traffic |
| `app/api/sales.py` | 5 Kanban/stage routes | replace with authoritative query/command adapter; retire |
| `app/web/admin/crm_leads.py` | 8 mounted Lead routes | exact owner known; behavior/data/caller/shadow/cutover/traffic/deletion gates remain |
| `app/web/admin/crm_quotes.py` | 13 mounted Quote routes | split query, authoring and acceptance at the exact owner; do not retain generic CRM lifecycle writes |
| `app/web/admin/crm_sales.py` | 16 mounted routes | ten Pipeline/Stage routes are sales; six SalesOrder routes belong to the separate orders workstream |
| `app/services/crm/portal_quotes.py` and `app/api/crm/portal.py` | customer Quote request/list/accept and deposit/downstream effects | re-point Quote decisions; keep downstream consequences separate; delete local Quote writer |

Sub's checked-in CRM web retirement ledger pins CRM
`87f6273d040a3c3cc27213801da80ee91d278673`. That revision is an ancestor of
the CRM evidence pin and the sales paths above are unchanged between them. The
ledger therefore remains the route-level retirement control. This document is
not a substitute for advancing each of its gates.

### Secondary writers

| Path | Current write | Scope-safe disposition |
| --- | --- | --- |
| `app/services/crm/contacts/service.py` | creates Lead | migrate to authoritative sales command after identity mapping |
| `app/services/crm/referrals.py` | creates Lead | referral owner calls sales contract; referral does not move into sales |
| `app/services/crm/serp_targets.py` | creates Lead | classify and migrate/remove caller |
| `app/services/erpnext/importer.py` | constructs Lead, Quote and lines and updates import state | typed import adapter; no provider logic in module |
| `app/services/crm/campaigns.py` | creates campaign-derived Lead | inventory only; campaign owner unverified and untouched by this migration |
| `app/services/crm/inbox/resolve_gate.py` | creates Lead from Inbox resolution | inventory only; Inbox behavior/ownership untouched |
| `app/services/meta_webhooks.py` | creates Lead and mutates source/attribution | inventory only; connector transport untouched |

### Dependent readers/consequences

Workqueue/report/search providers read CRM Lead/Quote rows. CRM portal templates
and forms assume local ids. `selfcare.notify_quote_event` sends best-effort
Quote events to Sub, and the Selfcare customer event handler reads Quote lines
to drive invoice/order/project effects. These are caller/consequence migration
obligations, not evidence that CRM owns sales or that sales owns downstream
rows.

## Parity and defect evidence

`tests/test_crm_sales_services.py` contains 79 tests covering Pipeline/Stage
CRUD, Lead CRUD/search/dedup/probability/Kanban/stage movement, Quote CRUD/
search/line arithmetic/status and legacy acceptance idempotence. Preserve each
active customer/operator behavior or record an explicit removal.

Do **not** port the CRM transaction shape:

- services commit and raise HTTP exceptions;
- rows have no tenant id, RLS or FORCE policy;
- accepted Quote headers/lines remain mutable and deletable;
- acceptance has no parent lock or durable owner output; and
- SalesOrder/Project effects occur after commit.

Sub behavior and tests take precedence where the sources differ.

## Retirement sequence

1. **External gate:** accepted Starter P11 evidence must show the kernel
   migration lineage running in a real product production database. CRM does
   not clear or reinterpret that gate.
2. **Module proof:** released `dotmac-sales`, all tenant/RLS/immutability/
   concurrency/output canaries green, installed and composed by Sub.
3. **Backfill:** copy CRM historical rows through reviewed mapping into the
   authoritative migration path; repeated runs are idempotent and report-only
   reconciliation changes nothing.
4. **Shadow:** compare keys, states, money, ordered lines and full-column typed
   digests. No dual writes.
5. **Sub switch:** seal the authority cutover under Starter ADR-0031; split
   accepted Quote from downstream consequences.
6. **CRM caller flip:** migrate every primary and secondary caller, then block
   CRM-local sales writes.
7. **Fallback retirement:** remove writer jobs/webhooks/import paths or turn
   them into adapters to the authoritative contract; prove the direct-writer
   ratchet is zero.
8. **Route retirement:** satisfy every Sub ledger gate, including a healthy
   30-day Loki plus metrics zero-traffic window, then delete the route/source.
9. **Data retirement:** requires separate production, retention and backup
   authorization. It is not authorized by this document.

## Required proof

- exact module/Sub/CRM release pins;
- full prescribed Starter and Sub validation;
- backfill identity/count/digest reports without customer values;
- shadow and report-only reconciliation results;
- same-transaction cutover evidence and effective privileges;
- two-directional writer baseline with an injected-writer sensitivity test;
- per-route caller, parity, cutover, fallback, traffic and deletion evidence;
  and
- no campaign, Inbox, connector, consent, retention or Orders row advanced by
  association with the sales cutover.

Until those proofs exist, CRM's sales writers are legacy active risk and this
retirement is **not complete**.
