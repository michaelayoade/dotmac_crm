# Findings — Ticket detail, GIS, Surveys, Data Quality, Workqueue

Screenshots: `../screenshots/15-ticket-detail.png`, `16-gis.png`.

---

## BUG-090 — Admin sidebar "Workqueue" link → `/agent/workqueue` returns raw JSON 403

**Severity:** BUG (medium — broken nav for admins + raw JSON to browser)

**Symptom:** The CRM sidebar (visible from admin pages) contains a **Workqueue**
link to `/agent/workqueue`. Navigating there as the admin returns a raw JSON body:

```json
{"code":"http_403","message":"Forbidden","details":null}
```

The agent workqueue requires an agent context the admin lacks. Either the link
should not be shown to non-agents, the admin should be permitted, or a styled
error/redirect should be returned instead of raw JSON (same class as BUG-051 and
NOTE-052).

---

## POSITIVE — Ticket detail page + inline status update (with audit)

`/admin/support/tickets/34` is feature-complete and clean (0 console errors):
description, comments (with "Polish with AI" + voice recording), attachments,
activity timeline, customer/subscriber linking, relationships (link outage /
merge), status & priority selectors, rule auto-assign, AI assistant
(Summary/Triage), ERP expenses, "Request Materials".

**Verified workflow:** changing the inline **Update Status** dropdown from Open →
Pending auto-saved (HTMX), added an Activity Timeline entry, and **persisted to
the DB** (`tickets.status = pending` confirmed). Good.

---

## POSITIVE — GIS, Surveys, Data Quality load cleanly

- `/admin/gis` ("GIS & Mapping") — 0 console errors.
- `/admin/surveys` ("Surveys") — loads.
- `/admin/data-quality` ("Data Quality") — loads.
