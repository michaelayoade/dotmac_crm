# DotMac Omni — QA Test Environment & Findings

This directory documents a dedicated, seeded QA environment for the DotMac Omni
CRM and the results of exploratory, Playwright-driven edge-case testing per
module.

## Environment overview

| Piece | Value |
|-------|-------|
| Test database | `crm_test` (inside the existing `dotmac_omni_db` PostGIS container) |
| Test app | `dotmac_omni_app_test` container, served at **http://localhost:8010** |
| Dev app (untouched) | `dotmac_omni_app` at http://localhost:8000 (uses `dotmac_omni` DB) |
| Admin login | username `qaadmin` / password `QaAdmin!2026Secure#Pass` |
| Vendor portal login | `vendoruser@test.local` / `VendorQA!2026Secure#Pass` (via `/vendor/auth/login`) |
| Reseller portal login | `reselleruser@test.local` / `ResellerQA!2026Secure#Pass` (via `/auth/login`) |
| Compose file | [`docker-compose.test.yml`](../../docker-compose.test.yml) |

The QA stack is fully isolated from your dev database and dev app. It reuses the
same Docker image, Redis (logical DB 5), and the `dotmac_omni_default` network.

## How the test DB was built

The squashed initial Alembic migration cannot build a fresh DB (see
[findings/00-environment-and-blockers.md](findings/00-environment-and-blockers.md),
BUG-001), so the schema was cloned from the dev DB:

```bash
# 1. Create the DB + PostGIS
docker exec dotmac_omni_db psql -U postgres -c "CREATE DATABASE crm_test OWNER dotmac_omni_app;"
docker exec dotmac_omni_db psql -U postgres -d crm_test -c \
  "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS postgis_topology;"

# 2. Clone schema from dev (correct FK ordering) + stamp alembic
docker exec dotmac_omni_db sh -c \
  "pg_dump -U postgres -d dotmac_omni --schema-only --no-owner --no-privileges | psql -U postgres -d crm_test -q"
docker exec dotmac_omni_db psql -U postgres -d crm_test -c \
  "INSERT INTO alembic_version (version_num) VALUES ('zu8f9a0b1c2d') ON CONFLICT DO NOTHING;"

# 3. Grant the app role access
docker exec dotmac_omni_db psql -U postgres -d crm_test -c \
  "GRANT ALL ON ALL TABLES IN SCHEMA public TO dotmac_omni_app;
   GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO dotmac_omni_app;"

# 4. Copy domain_settings config (app startup seeder crashes on empty DB — BUG-002)
docker exec dotmac_omni_db sh -c \
  "pg_dump -U postgres -d dotmac_omni --data-only --table=public.domain_settings | psql -U postgres -d crm_test -q"
```

## How the data was seeded

```bash
TESTDB="postgresql+psycopg://dotmac_omni_app:<app-pw>@db:5432/crm_test"

# Admin + RBAC
docker exec -e DATABASE_URL="$TESTDB" -e PYTHONPATH=/app -w /app dotmac_omni_app \
  python scripts/seed_admin.py --email qaadmin@test.local --first-name QA --last-name Admin \
  --username qaadmin --password 'QaAdmin!2026Secure#Pass'
docker exec -e DATABASE_URL="$TESTDB" -e PYTHONPATH=/app -w /app dotmac_omni_app \
  python scripts/seed_rbac.py --admin-email qaadmin@test.local

# Rich edge-case dataset (scripts/seed_test_data.py — idempotent, sentinel-guarded)
docker cp scripts/seed_test_data.py dotmac_omni_app:/app/scripts/seed_test_data.py
docker exec -e DATABASE_URL="$TESTDB" -e PYTHONPATH=/app -w /app dotmac_omni_app \
  python scripts/seed_test_data.py
```

The seed script ([`scripts/seed_test_data.py`](../../scripts/seed_test_data.py))
deliberately includes edge cases: unicode names, HTML/SQL-injection-shaped
strings, emoji, very long names, missing optional fields, and records spanning
every status/priority enum for tickets, leads, and conversations.

## Start / stop the test app

```bash
docker compose -p dotmac_omni_test -f docker-compose.test.yml up -d
docker compose -p dotmac_omni_test -f docker-compose.test.yml down
```

## Findings index

Start with **[SUMMARY.md](SUMMARY.md)** — findings register + coverage matrix.

| Doc | Module(s) |
|-----|-----------|
| [SUMMARY.md](SUMMARY.md) | Roll-up: all findings + coverage matrix |
| [00-environment-and-blockers.md](findings/00-environment-and-blockers.md) | Setup, fresh-DB migration & startup bugs |
| [01-crm-contacts.md](findings/01-crm-contacts.md) | CRM › Contacts (escaping, pager, currency) |
| [02-tickets.md](findings/02-tickets.md) | Support › Tickets |
| [03-crm-leads.md](findings/03-crm-leads.md) | CRM › Leads (pipeline value, currency) |
| [04-network-map-and-cross-cutting-js.md](findings/04-network-map-and-cross-cutting-js.md) | Network map + cross-cutting `block scripts` JS defect |
| [05-portals.md](findings/05-portals.md) | Customer / Vendor / Reseller portals |
| [06-quotes-sales-leadform.md](findings/06-quotes-sales-leadform.md) | Quotes, Sales Dashboard, Lead form |
| [07-operations-materials-inventory.md](findings/07-operations-materials-inventory.md) | Material Requests, Inventory |
| [08-system-audit-campaigns-automations.md](findings/08-system-audit-campaigns-automations.md) | Audit log, Campaigns, Automations |
| [09-ticket-detail-gis-workqueue.md](findings/09-ticket-detail-gis-workqueue.md) | Ticket detail/workflow, GIS, Surveys, Data Quality, Workqueue |
| [10-subscribers-projects-dispatch.md](findings/10-subscribers-projects-dispatch.md) | Subscribers, Projects, Dispatch, Intelligence |
| [11-vendors-teams-integrations-retention.md](findings/11-vendors-teams-integrations-retention.md) | Vendors, User Groups, Integrations, Performance, Retention |
| [12-security-xss-and-forms.md](findings/12-security-xss-and-forms.md) | Security (reflected/stored XSS), create-form validation |
| [13-exhaustive-route-sweep.md](findings/13-exhaustive-route-sweep.md) | **Exhaustive** sweep of all 157 admin pages (2 × 500 found) |
| [14-portals-and-access-control.md](findings/14-portals-and-access-control.md) | Vendor + Reseller portals; **CRITICAL broken access control** (BUG-140) |
| [15-input-robustness.md](findings/15-input-robustness.md) | Query-param / injection robustness (all handled) |
| [16-mutation-lifecycles.md](findings/16-mutation-lifecycles.md) | Create/edit/delete lifecycles (DB-verified) + data-corruption bug |

## Severity legend

- **BUG** — incorrect behavior, error, or data corruption
- **UX** — confusing/awkward but not broken
- **A11Y** — accessibility gap
- **NOTE** — observation / works-as-designed caveat
