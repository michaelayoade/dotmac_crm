# Findings — CRM › Contacts (`/admin/crm/contacts`)

Screenshot: `../screenshots/02-contacts.png`
Seed data: 13 contacts incl. unicode, emoji, HTML/SQL-injection-shaped, long, and
minimal-field names.

---

## BUG-010 — "Leads" sidebar link hardcoded to the production domain

**Severity:** BUG (high)

**Where:** `templates/components/navigation/admin_sidebar.html:256`

```html
<a href="https://crm.dotmac.io/admin/crm/leads" ...>
```

**Symptom:** Every other sidebar entry uses a relative path (`/admin/crm/...`),
but the **Leads** link is hardcoded to `https://crm.dotmac.io/admin/crm/leads`.
On the QA env (`localhost:8010`) — or any staging/preview/self-hosted instance —
clicking **Leads** navigates the operator off the current environment and into
production.

**Impact:** Cross-environment navigation; on a customer self-hosted deployment
the link leaks the DotMac SaaS URL and breaks the nav. Confusing and potentially
a data-context leak (operator may act on prod thinking they're on staging).

**Suggested fix:** Use the relative path `/admin/crm/leads` (the dashboard's
"Open Leads" card already links there correctly).

> Related: `app/services/crm/inbox/conversation_status.py:31` also hardcodes a
> `https://crm.dotmac.io/...` feedback URL — confirm that is intentional for
> self-hosted installs.

---

## BUG-011 — Pagination shows "Page 1 of 1" but renders active Prev/Next links

**Severity:** BUG (medium)

**Where:** Contacts list pager (and likely the shared pagination macro).

**Symptom:** With 13 contacts at 25/page there is exactly one page — the footer
correctly says **"Page 1 of 1"** — yet both **Prev** and **Next** are rendered as
active links:

- Prev → `?page=0&per_page=25...`
- Next → `?page=2&per_page=25...`

**Impact:** Clicking Next goes to an empty page 2; Prev uses `page=0` while the
current page is labeled `1`, exposing inconsistent 0- vs 1-based indexing.

**Suggested fix:** Disable/hide Prev on the first page and Next on the last page;
normalize page indexing (the label, the Prev target, and the Next target disagree).

---

## POSITIVE — Output is correctly HTML-escaped (no stored XSS)

The contact named `Bobby <b>Tables</b>` (display) / `Robert '); DROP TABLE--`
(last name) renders as **literal text** in the table — the `<b>` is not
interpreted and the SQL fragment is inert. Unicode (`李伟`, `José Müller`,
`Søren Kierkegaard`) and emoji (`🚀 Rocket User 😀`) all render correctly. Good.

---

## NOTE-012 — Pipeline/value currency symbol is fixed to ₦ regardless of lead currency

**Severity:** NOTE

Leads were seeded with `currency = USD`, but the dashboard "Pipeline Value" and
the leads summary render the total as **₦** (Naira). The display currency appears
hardcoded/locale-fixed rather than derived from the records. Confirm whether
multi-currency is in scope; if so, the symbol should follow the data.

---

## NOTE-013 — Whitespace-padded names are trimmed on display

The contact seeded as `"  Padded  Name  "` displays as `Padded Name`. Reasonable,
but worth confirming the trimming happens on save (canonical data) vs only on
display.
