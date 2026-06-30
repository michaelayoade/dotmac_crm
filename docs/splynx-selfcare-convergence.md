# Splynx → Selfcare Keying Convergence

Migrated subscribers are keyed in CRM under `external_system="splynx"` by their
legacy Splynx customer id; native subscribers under `selfcare`/`dotmac` by the
dotmac_sub UUID. As of this writing dotmac_sub holds **~15,263 migrated (splynx)
vs ~18 native** subscribers, so the legacy keying dominates. This doc records
how the two converge and the gate for retiring the splynx-specific code.

## The re-key already runs (do not write a new migration)

`app/services/selfcare.py::sync_subscribers_from_selfcare_data` (the scheduled
`app.tasks.subscribers.sync_subscribers_from_selfcare` job) pulls every
subscriber from dotmac_sub keyed by the sub UUID + `subscriber_number`. When it
finds an existing CRM row by `subscriber_number` that is **unowned or owned by
`selfcare`/`splynx`**, it adopts and **re-keys it to `external_system="selfcare"`**
with the dotmac_sub UUID, and `sync_from_external` backfills
`people.metadata.selfcare_id`. It refuses to re-key a row owned by a different
live system.

So convergence is an in-flight process driven by that sync — **not** a one-off
data migration. A blind re-key script would be redundant and risky. The prior
migration `sc2026062700` deliberately relabelled only cosmetic source strings
(`splynx_polling` → `selfcare_polling`) and left `external_id` remapping to this
live bridge.

## Gate: measure before retiring code

`scripts/splynx_convergence_status.py` (→ `app/services/splynx_convergence.py`,
read-only) reports, against the CRM DB:

- `subscribers_by_external_system` — keying breakdown.
- `subscribers_remaining_splynx` — rows still to re-key (**target 0**).
- `people_splynx_id_without_selfcare_id` — identity rows not yet backfilled
  (**target 0**).
- `converged` — true when both are 0.

### Ops steps
1. Confirm `integration.selfcare_subscriber_sync_enabled` is **on** in prod
   (default is off) and the job completes across the full base (mind the
   pagination caps / orphan guards).
2. Run the status script; watch the remaining counts trend to 0.
3. Only when `converged` is true, retire the legacy code below.

## Retirement checklist (gated on `converged == true`)

Code that becomes dead once no splynx-keyed rows remain (see the audit for
exact citations):

- `Person.splynx_id` hybrid property (and the `splynx_id`-preferring fallback).
- The `metadata_key = "selfcare_id" if … else "splynx_id"` fork in
  `subscriber.py` — standardize on `selfcare_id`.
- `_handle_splynx_webhook` routing + the `map_splynx_customer_to_subscriber_data`
  alias (the mapper is already identical to selfcare's).
- `splynx_id` contact form fields / schema, and the `splynx`/`splynx_live`
  display + source labels.
- `external_systems.SPLYNX_EXTERNAL_SYSTEM` and the `server_default="splynx"` on
  `subscribers.external_system`.
- Splynx-tagged test fixtures.

Each is additive-safe to remove only after the data is verified converged;
until then they remain correct legacy handling, not bugs.
