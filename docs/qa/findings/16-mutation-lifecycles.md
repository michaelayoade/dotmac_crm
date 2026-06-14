# Findings — Mutation lifecycles (create / edit / delete), DB-verified

Drove full write lifecycles through the UI and verified each state in `crm_test`.

---

## BUG-150 — Inventory item edit form persists literal "None" into Description (data corruption)

**Severity:** BUG (medium-high — writes bad data on every edit)

**Repro:**
1. Item with `description = NULL`.
2. Open `/admin/inventory/items/<id>/edit` — the **Description** textarea is
   pre-filled with the literal string **`None`** (Python `None` rendered into the
   field value).
3. Change any other field and Save (without touching Description).
4. **DB now stores `description = 'None'`** (verified:
   `inventory_items.description = 'None'`).

Unlike the display-only None-leaks (BUG-080/101), this one is in an **editable
field**, so it round-trips and corrupts data — every edit of an item with a null
description silently writes the string "None". Over time, descriptions, notes,
etc. across edit forms accumulate "None".

**Suggested fix:** Render `{{ value or '' }}` (or `default('', true)`) for form
field values; audit all edit-form templates for the same pattern.

---

## BUG-151 — Contact create: `.local` emails rejected with a raw pydantic error leaking the schema name

**Severity:** BUG (low-medium — UX + inconsistency)

Creating a contact with email `lifecycle@test.local` fails with this message shown
to the user verbatim:

> `1 validation error for ContactCreate email value is not a valid email address:
> The part after the @-sign is a special-use or reserved name that cannot be used
> with email. [type=value_error, input_value='lifecycle@test.local', ...]`

Two issues:
1. **Raw error leakage** — the internal schema class name (`ContactCreate`),
   pydantic type tags, and input value are surfaced to the end user. Should be a
   friendly field-level message ("Enter a valid email address").
2. **Inconsistency** — the contact schema uses strict email validation (rejects
   `.local`/reserved TLDs), yet seeded contacts and the **admin login**
   (`qaadmin@test.local`) use `.local`. Internal/B2B deployments often use such
   domains; confirm whether this strictness is intended.

(The form otherwise behaves well: it re-renders with values preserved and the
error visible — good PRG-with-errors handling.)

---

## BUG-071 (confirmed via UI) — Duplicate SKU accepted by the inventory create form

Creating an item with SKU `FIB-SC-001` (already present twice) succeeded with **no
warning** — there are now **three** items with that SKU. Confirms the missing
uniqueness constraint end-to-end through the UI, not just at the data layer.

---

## POSITIVE — Full CRUD lifecycles work and persist correctly

| Flow | Steps verified (DB-checked) |
|------|------------------------------|
| **CRM contact** | create (`is_active=t`) → edit (name + city updated) → **deactivate** (`is_active=f`) |
| **Inventory item** | create → edit (name updated) |
| **Ticket comment** | post → persisted in `ticket_comments` |
| **Ticket status** (earlier) | inline change Open→Pending persisted + audited |

- All mutations use **PRG** (303 redirect after POST).
- Deactivate uses a **styled confirmation modal** ("Confirm Deactivate") — good UX,
  not a native `confirm()`.
- Edit forms pre-populate correctly (except the None-leak in BUG-150).

---

## POSITIVE — Stored XSS in ticket comments is escaped

Posted a comment `<script>window.__cxss=1</script>Lifecycle QA comment`. The script
did **not** execute (`window.__cxss` unset, no live `<script>` in the comment DOM);
the payload is HTML-escaped (`&lt;script&gt;…`) while the text persists in
`ticket_comments`. Output encoding holds for comments (consistent with ticket
titles, BUG-free).
