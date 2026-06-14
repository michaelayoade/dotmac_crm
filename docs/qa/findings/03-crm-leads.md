# Findings — CRM › Leads (`/admin/crm/leads`)

Screenshot: `../screenshots/04-leads.png`
Seed data: 7 leads, one per `LeadStatus` (New→Lost), values 1,000–7,000 USD.

---

## BUG-030 — "Pipeline Value" includes Lost (and Won) leads

**Severity:** BUG (medium-high — misreports a core sales metric)

**Symptom:** The Leads page header shows **Pipeline Value = 28,000**, which is the
sum of *all 7* leads (1k+2k+…+7k). That total includes the **Lost** lead (7,000)
and the **Won** lead (6,000). Pipeline value should reflect *open/active*
opportunities only; a Lost lead must never count toward pipeline.

**Cross-check:** The **dashboard** "Pipeline Value" for the same data shows
**15,000** (the 5 open leads: 1k+2k+3k+4k+5k) — i.e. the dashboard computes it
correctly while the Leads page does not. The two screens disagree.

**Suggested fix:** Exclude `won` and `lost` (at minimum `lost`) from the Leads
page pipeline-value aggregate, matching the dashboard definition.

---

## BUG-031 — Inconsistent currency formatting across three surfaces

**Severity:** BUG (low-medium)

Same lead data renders its monetary total three different ways:

| Surface | Render |
|---------|--------|
| Lead row "Value" | `USD 7,000.00` (correct, per-record currency) |
| Leads page "Pipeline Value" | `28,000` (no currency symbol/code) |
| Dashboard "Pipeline Value" | `₦15,000` (hardcoded Naira) |

The dashboard shows ₦ for USD-denominated leads (see NOTE-012), and the Leads
summary shows no currency at all. Pick one canonical money formatter.

---

## BUG-011 (confirmed shared) — Pager shows "Page 1 of 1" with active Prev/Next

The Leads list reproduces the Contacts pager defect exactly: footer says
"Page 1 of 1" yet renders active **Prev** (`page=0`) and **Next** (`page=2`)
links on a single-page result. Since it appears on both Contacts and Leads, this
is the **shared pagination macro/partial**, not a one-off. (The Tickets list uses
a different "Showing X to Y of N" pager and is not affected.)

---

## POSITIVE — Status badges, per-record currency, unicode contacts

All 7 `LeadStatus` values render; contact names with unicode (`李伟`,
`José Müller`, `Søren Kierkegaard`) display correctly; owner/source/status filters
are populated.
