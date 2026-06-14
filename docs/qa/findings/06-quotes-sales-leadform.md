# Findings — CRM › Quotes, Sales Dashboard, Lead form

Screenshots: `../screenshots/07-quotes.png`, `08-quote-new.png`,
`09-lead-detail.png`, `10-sales-dashboard.png`.

---

## BUG-040 (confirmed across areas) — Sales Dashboard & New Lead form throw the Alpine `locationShareToggle` errors

**Severity:** High (same root cause as `04-network-map-...`)

Verified the identical 6-error console signature (`locationShareToggle`/`init`/
`loading`/`isSharing` `ReferenceError`) on:

- `/admin/crm/sales` (Sales Dashboard — `sales_dashboard.html`)
- `/admin/crm/leads/new` (New Lead form — `lead_form.html`)

This confirms the `{% block scripts %}`-without-`{{ super() }}` defect is **not**
limited to the network map; it spans CRM too. Pages that are clean (call super or
don't override): Quotes list, New Quote form, Lead **detail** page (0 errors).

---

## POSITIVE — Quotes empty state is well-handled

`/admin/crm/quotes` with zero quotes shows a proper empty state ("No quotes
found… Create a new quote to get started") with a CTA, and 0 console errors.
Status stat-cards all read 0. Good.

---

## POSITIVE — New Lead required-field validation blocks empty submit

Submitting the New Lead form with no Person selected does **not** navigate/POST —
the required `Person *` field blocks submission client-side. Good.

---

## POSITIVE — Injection-shaped name escaped inside `<option>`

The person picker lists `Bobby <b>Tables</b> (bobby.seed@test.local)` as literal
text in the `<option>` (not interpreted) — escaping holds in select options too.

---

## NOTE-060 — Lead form currency defaults to `NGN`; region list is Abuja-centric

The New Lead "Currency" field defaults to `NGN` and the Region dropdown lists
Gudu/Garki/Gwarimpa/Jabi/Lagos. Consistent with the app's Nigeria/Naira
assumption (see NOTE-012/BUG-031). Confirm whether currency/region should be
configurable for non-NGN deployments.
