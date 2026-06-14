# Findings — Operations: Material Requests & Inventory

Screenshots: `../screenshots/11-material-requests.png`,
`12-material-request-detail.png`.

---

## BUG-071 — Inventory SKU has no uniqueness constraint (duplicate SKUs allowed)

**Severity:** BUG (medium — data integrity)

**Where:** `app/models/inventory.py:28` — `sku: Mapped[str | None] =
mapped_column(String(80))` (no `unique=True`, no unique index).

**Evidence (crm_test):** five SKUs each exist twice with different IDs:

```
FIB-SC-001 ×2, PP-24-001 ×2, ONT-GP-100 ×2, CAB-DR-100 ×2, CON-SCAPC-1 ×2
```

`\d inventory_items` shows an empty `Indexes:` section — no unique constraint on
`sku`. Two inventory items can share a SKU, which breaks SKU-based lookups,
reporting, and ERP sync expectations.

**Suggested fix:** Add a unique constraint/index on `sku` (partial unique index
allowing multiple NULLs if SKU is optional), plus de-dupe existing data via
migration. Validate at the service layer too.

> Note: this surfaced because the seed legitimately ran twice; the app *allowed*
> the duplicates rather than rejecting them — that's the finding.

---

## BUG-070 — Material Request "Add Item" requires pasting a raw inventory UUID

**Severity:** UX (medium)

**Where:** Material Request detail → "Add Item" panel.

The "Item ID" field is a free-text box with placeholder **"Inventory item UUID"**.
Operators must paste a UUID to add a line item — there is no searchable item
picker (the inventory list, by contrast, links items by name). This is
error-prone and unusable without copying IDs from another screen.

**Suggested fix:** Replace with a typeahead/select bound to inventory items
(name + SKU), like the person picker on the Lead form.

---

## POSITIVE — Material Request workflow + detail page

- List shows seeded request (Draft, Medium, requester "Ada Lovelace", 2 items).
- Detail page links to the originating ticket (#9), shows items with SKU/qty,
  priority, requester, timestamps.
- **State transition works:** clicking "Submit for Approval" moved Draft →
  Submitted, revealed the "Issue Settings" panel (warehouse selectors + "Issue &
  Sync to ERP") and the Reject/Cancel actions, and recorded a "Submitted"
  timestamp.

---

## NOTE-072 — No warehouses; Unit column blank

Inventory shows 0 warehouses and "Total On Hand" 0, and every item's "Unit"
column is "—". The Issue Settings warehouse dropdowns are therefore empty
("Select warehouse…" only). Expected given no warehouse seed; flagged so a future
pass can exercise the issue-to-warehouse flow.

---

## NOTE-073 — `/admin/material-requests` 404s; real path is `/admin/operations/material-requests`

Confirm navigation/links never use the bare `/admin/material-requests` form.
