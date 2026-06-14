# Findings — Environment & Infrastructure

Discovered while standing up the seeded `crm_test` QA environment.

---

## BUG-001 — Fresh database cannot be built from migrations (FK ordering in squashed initial migration)

**Severity:** BUG (high — blocks any clean install / new environment)

**Where:** `alembic/versions/af8fbbefa221_initial_schema.py`

**Symptom:** `alembic upgrade heads` against an empty database fails:

```
psycopg.errors.UndefinedTable: relation "vendors" does not exist
sqlalchemy.exc.ProgrammingError: ... while creating "installation_projects"
```

**Cause:** In the squashed initial migration, `installation_projects` is created
at line ~359 but it has a foreign key to `vendors`, which is not created until
line ~700. On a fresh DB the FK target does not yet exist. The existing dev DB
only works because it was migrated incrementally *before* the squash.

**Impact:** Any new deployment, CI ephemeral DB, or contributor spinning up the
stack from scratch will fail at migration time. This is why the QA DB schema had
to be cloned from the dev DB via `pg_dump` instead.

**Suggested fix:** Reorder `op.create_table` calls in the initial migration so
referenced tables (`vendors`, etc.) are created before referencing tables, or add
the FK via a later `op.create_foreign_key` after both tables exist.

---

## BUG-002 — App startup crashes on an empty database (workflow settings seeder)

**Severity:** BUG (high — blocks first boot against a fresh DB)

**Where:** `app/services/settings_seed.py:715` (`seed_workflow_settings`) →
`app/services/domain_settings.py:144` (`ensure_by_key`).

**Symptom:** With an empty `domain_settings` table, app startup raises:

```
pydantic_core.ValidationError: 1 validation error for DomainSettingCreate
  Value error, non-json settings require value_text.
ERROR:    Application startup failed. Exiting.
```

**Cause:** `ensure_by_key(...)` builds a `DomainSettingCreate` for a non-JSON
setting without providing `value_text`, which the schema validator rejects. On
the dev DB this code path is never hit because the rows already exist and the
seeder short-circuits; it only fails on a truly fresh DB.

**Impact:** A clean install cannot boot until `domain_settings` is pre-populated.
Worked around for QA by copying `domain_settings` rows from the dev DB.

**Suggested fix:** In `seed_workflow_settings` (or `ensure_by_key`), pass a
`value_text` default for non-JSON settings, or make `ensure_by_key` tolerate the
missing default by deriving `value_text` from the provided default value.

---

## NOTE-003 — `people.email` is NOT NULL at the DB level

**Severity:** NOTE

**Where:** `people` table.

**Observation:** A `Person` cannot be created without an email — the column is
`NOT NULL`. Seeding a contact with no email aborts the insert. If product intent
is that contacts may exist without an email (e.g. phone-only / walk-in leads),
this constraint conflicts with that. Confirm whether email should be optional for
non-portal contacts.
