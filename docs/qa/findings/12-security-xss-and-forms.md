# Findings — Security (XSS) & Create-form validation

Focused tests for reflected/stored XSS and form behavior.

---

## POSITIVE — No reflected XSS in search

Navigating `/admin/crm/contacts?search=<img src=x onerror=window.__xss=1>`:

- `window.__xss` was **not** set (payload did not execute).
- No live `<img onerror>` element was created (payload present only as escaped
  text).

Reflected search input is safe.

---

## POSITIVE — No stored XSS in ticket title

Created a ticket with title `<img src=x onerror=window.__xss2=1>Bobby Tables Ticket`
(ticket #35). On the resulting detail page:

- `window.__xss2` not set (no execution).
- No live `<img onerror>`; the title is HTML-escaped in the DOM
  (`&lt;img src=x onerror…`).

Stored output encoding holds for ticket titles.

> Test artifact: ticket **#35** in `crm_test` now carries this payload as inert
> text — handy as an escaping regression fixture; delete if undesired.

---

## POSITIVE — Ticket create flow + required validation

- New Ticket form enforces `Title *` and `Ticket Type *`.
- Submitting valid data created the ticket and **PRG-redirected** to
  `/admin/support/tickets/35` (303 pattern), as required.

---

## NOTE-120 — Contacts search term not reflected back into the search box

After loading the contacts list with `?search=<term>`, the filter "Search" input
renders **empty** (the active query is not echoed back into the field). Minor UX:
the user can't see what the current results are filtered by. Confirm whether the
search box should be pre-filled with the active `search` value (the status/type
filter chips do reflect state).
