# Findings — Vendors, User Groups, Integrations, Performance, Retention

Screenshot: `../screenshots/19-customer-retention.png`.

This sweep was mostly healthy; the recurring theme is missing index routes.

---

## NOTE-110 — More section landing pages 404 (no index route)

Several sidebar sections have no bare index and 404 on direct navigation; the
real pages live under sub-paths (sidebar links are correct, but the pattern is
inconsistent and brittle):

| Guessed | Actual |
|---------|--------|
| `/admin/integrations` (404) | `/admin/integrations/connectors`, `/targets` |
| `/admin/service-teams` (404) | `/admin/system/teams` ("User Groups") |
| `/admin/performance` | redirects → `/admin/performance/team` (good) |

Combined with NOTE-081 / NOTE-102 (reports, network, intelligence, automations,
material-requests), this is a systemic inconsistency. Recommend adding index
routes that redirect to the first sub-page (as `/admin/performance` already does),
so deep links and muscle-memory URLs don't dead-end.

---

## POSITIVE — All loaded cleanly (0 console errors)

- `/admin/vendors` — "Vendors"
- `/admin/system/teams` — "User Groups"
- `/admin/performance/team` — "Team Performance"
- `/admin/integrations/connectors` — "Connectors"
- `/admin/customer-retention` — "Customer Retention Tracker"
