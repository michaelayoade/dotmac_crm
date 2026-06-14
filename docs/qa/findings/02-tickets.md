# Findings — Support › Tickets (`/admin/tickets` → `/admin/support/tickets`)

Screenshot: `../screenshots/03-tickets.png`
Seed data: 26 tickets spanning every `TicketStatus` and `TicketPriority`.

---

## POSITIVE — Status/priority badges and list render cleanly

- All statuses render with badges + icons: Open, Pending, On Hold, Closed, New,
  Waiting (`waiting_on_customer`), Rerun (`lastmile_rerun`), Construction
  (`site_under_construction`). Long enum values are shortened sensibly.
- `/admin/tickets` correctly redirects to `/admin/support/tickets`.
- Sortable columns (Priority/Status/Opened) and row actions (View/Edit/Delete) present.
- Pagination footer reads "Showing 1 to 20 of 20 results" with **no** spurious
  Prev/Next links — so the pager defect (BUG-011) is specific to the Contacts
  list template, not the shared behavior.

---

## NOTE-020 — Default list hides terminal-status tickets (count mismatch vs total)

**Severity:** NOTE (likely by design — confirm)

26 tickets exist, but the list shows "20 of 20". The 6 hidden ones are the
terminal statuses (closed/canceled/merged, 2 each). The status stat-cards
(Open 8 / Pending 2 / On Hold 2 / Closed 2) only cover a subset of the existing
statuses. Confirm this is the intended default filter and that operators have an
obvious way to view closed/canceled/merged tickets (a "Closed" card exists, good).

---

## NOTE-021 — Customer & Type columns empty for ticket-only records

Tickets seeded without a linked customer/type show "—" in both columns, which is
the correct empty treatment. Flagged only so the contrast with linked tickets can
be verified in a later pass (seed currently links none).
