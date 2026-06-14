# Findings — System (Audit) & Campaigns / Automations

Screenshots: `../screenshots/13-audit.png`, `14-automations.png`.

---

## BUG-080 — Audit Log filter inputs render the literal string "None"

**Severity:** BUG (low-medium — functional/UX)

**Where:** `templates/admin/system/audit.html:15,20,25`

```html
<input name="actor_id"     value="{{ actor_id | default('') }}" ...>
<input name="action"       value="{{ action | default('') }}" ...>
<input name="entity_type"  value="{{ entity_type | default('') }}" ...>
```

**Symptom:** On first load all three filter boxes contain the literal text
**`None`** (observed values: Actor ID = `None`, Action = `None`, Entity = `None`).

**Cause:** Jinja's `default('')` only substitutes for *undefined* variables, not a
defined Python `None`. The route passes `actor_id=None` (etc.), so `default('')`
is a no-op and `None` is stringified into the input `value`.

**Impact:** The user sees "None" pre-filled; clicking "Apply Filters" without
clearing posts `actor_id=None` and can filter the log incorrectly / return
nothing.

**Suggested fix:** Use `default('', true)` (replace falsy too) or `{{ actor_id or '' }}`
for all three inputs.

---

## POSITIVE — Audit trail captures actions correctly

The audit log recorded the material-request state change performed during this
QA pass:

- `QA Admin — Submit — Material Request #b4b5e82c (ID …)` — Success
- `QA Admin — Created — Operations Material Requests Submit` — Success

plus the auth-login events. Actor, entity, action, timestamp, and status all
populate.

---

## POSITIVE — Campaigns & Automations load cleanly

- `/admin/crm/campaigns` — renders, 0 console errors.
- `/admin/system/automations` — renders, 0 console errors.

---

## NOTE-081 — Inconsistent route prefixes for related "operations/system" pages

Several pages live under non-obvious prefixes, which made direct navigation 404
during testing (the sidebar links are correct, but bare guesses fail):

| Page | Actual path |
|------|-------------|
| Automations | `/admin/system/automations` (not `/admin/automations`) |
| Material Requests | `/admin/operations/material-requests` (not `/admin/material-requests`) |
| Data Quality | `/admin/data-quality` |
| Reports | no `/admin/reports` index (sub-paths only) |

Not a bug per se, but worth a consistency review / index routes that redirect.
