# Findings — Subscribers, Projects, Dispatch, Intelligence

Screenshots: `../screenshots/17-subscribers.png`, `18-project-detail.png`.

---

## BUG-100 — Subscribers list throws `allSelected is not defined` (bulk select-all outside its `x-data` scope)

**Severity:** BUG (medium — JS error, broken select-all/bulk actions)

**Symptom:** `/admin/subscribers` console error:

```
ReferenceError: allSelected is not defined
  at [Alpine] allSelected()   (x-bind:checked on the select-all header checkbox)
```

**Cause:** `allSelected()` is defined only inside the `data_table` macro's bulk
`x-data` block (`templates/components/ui/macros.html:375`, gated by
`{% if enable_bulk %}`). But `table_head` / `sortable_table_head` emit
`x-bind:checked="allSelected()"` (macros.html:519, 554) whenever
`selectable=True`. The Subscribers list renders a selectable header **without**
the enclosing bulk `x-data` scope, so `allSelected()` is unbound.

Contrast: Contacts and Tickets lists have working select-all (0 errors) because
they provide the bulk `x-data` wrapper.

**Suggested fix:** Either wrap the Subscribers table in the bulk-enabled
`data_table` (so the `x-data` is present), or make `table_head`'s select-all
checkbox degrade gracefully when `allSelected` is absent (e.g. guard with
`typeof allSelected === 'function'`).

---

## BUG-101 — Projects list renders literal "None" as the project subtitle

**Severity:** BUG (low — display; same class as BUG-080)

Each project row shows the name plus a second line reading **"None"** (the
project code/number is null and stringified). E.g. "Tower build - Lekki" / "None".
Another Python-`None`-into-template leak. Render an empty string (or omit the
line) when the code is missing.

---

## POSITIVE — Projects (multi-view), project detail, Dispatch board

- Projects list offers Table / Kanban / Gantt views, status stat-cards, human
  project codes (PROJ-5…PROJ-12), sortable columns.
- Project detail (`/admin/projects/PROJ-12`) loads with 0 console errors.
- Dispatch board (`/admin/operations/dispatch`) loads with 0 console errors.

---

## NOTE-102 — `/admin/intelligence` has no index (404)

Intelligence has no bare index route (sub-paths only). Same pattern as
`/admin/reports`, `/admin/network` (see NOTE-081).
