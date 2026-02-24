---
name: add-csv-importer
description: Scaffold a bulk CSV import script with validation, deduplication, dry-run, and error reporting
arguments:
  - name: import_info
    description: "Entity and fields to import (e.g. 'fiber closures with code, type, lat, lng from KMZ export')"
---

# Add CSV Importer

Scaffold a bulk CSV import script for DotMac Omni CRM.

## Steps

### 1. Understand the request
Parse `$ARGUMENTS` to determine:
- **Target entity**: the model/table to import into
- **CSV columns**: field mapping from CSV headers to model fields
- **Dedup key**: which field(s) identify duplicates (e.g. `email`, `sku`, `code`)
- **Normalization**: title case names, lowercase emails, strip whitespace
- **Foreign keys**: if any columns need to resolve IDs (e.g. `role_name` -> `role_id`)

### 2. Study the existing patterns
Read these reference scripts:

- **User import**: `scripts/bulk_create_agent_users.py` -- Person + UserCredential + Role assignment, password generation, CSV results report
- **Inventory import**: `scripts/bulk_import_inventory.py` -- Simple SKU-based dedup, `--update-existing` flag, dry-run support
- **Fiber KMZ import**: `scripts/import_fiber_kmz.py` -- Geographic data import with PostGIS
- **Notification seeder**: `scripts/seed_notification_templates.py` -- Idempotent upsert pattern

### 3. Create the import script
Create `scripts/import_{entity_plural}.py`:

```python
#!/usr/bin/env python3
"""Bulk import {entities} from CSV file.

CSV format:
    {col1},{col2},{col3},...

Example:
    {example_row_1}
    {example_row_2}

Usage:
    python scripts/import_{entity_plural}.py --csv scripts/{entity_plural}.csv
    python scripts/import_{entity_plural}.py --csv scripts/{entity_plural}.csv --dry-run
    python scripts/import_{entity_plural}.py --csv scripts/{entity_plural}.csv --update-existing
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.models.{module} import {Model}  # noqa: E402

logger = logging.getLogger(__name__)


# -- Normalization helpers ---------------------------------------------------

def _normalize_whitespace(value: str) -> str:
    """Collapse multiple spaces and strip."""
    return " ".join(value.strip().split())


def _title_case_name(value: str) -> str:
    """Title-case a name, handling hyphens and apostrophes."""
    value = _normalize_whitespace(value)
    if not value:
        return value
    parts = []
    for word in value.split(" "):
        hyphen_parts = []
        for part in word.split("-"):
            apostrophe_parts = [p.capitalize() for p in part.split("'")]
            hyphen_parts.append("'".join(apostrophe_parts))
        parts.append("-".join(hyphen_parts))
    return " ".join(parts)


def _normalize_email(value: str) -> str:
    return _normalize_whitespace(value).lower()


# -- Row dataclass -----------------------------------------------------------

@dataclass(frozen=True)
class ImportRow:
    """Validated row from CSV."""
    {field_1}: str
    {field_2}: str
    # Add fields matching CSV columns


# -- CSV loading -------------------------------------------------------------

def load_rows(csv_path: str) -> list[ImportRow]:
    """Load and validate rows from CSV, skipping duplicates."""
    rows: list[ImportRow] = []
    seen_keys: set[str] = set()
    skipped = 0

    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)

        # Validate headers
        expected = {"{col1}", "{col2}"}
        if reader.fieldnames:
            actual = set(reader.fieldnames)
            missing = expected - actual
            if missing:
                logger.error("CSV missing required columns: %s", missing)
                sys.exit(1)

        for line_num, raw in enumerate(reader, start=2):
            # Extract and normalize
            key_raw = (raw.get("{dedup_col}") or "").strip()
            if not key_raw:
                skipped += 1
                continue

            key = key_raw.lower()  # or other normalization
            if key in seen_keys:
                skipped += 1
                continue
            seen_keys.add(key)

            rows.append(ImportRow(
                {field_1}=_normalize_whitespace(raw.get("{col1}") or ""),
                {field_2}=_normalize_whitespace(raw.get("{col2}") or ""),
            ))

    if skipped:
        logger.info("Skipped %d rows (empty/duplicate)", skipped)
    return rows


# -- CLI arguments -----------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk import {entities} from CSV.")
    parser.add_argument(
        "--csv",
        required=True,
        help="Path to CSV file with required columns.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing to the database.",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update existing records (matched by {dedup_col}) instead of skipping.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser.parse_args()


# -- Main import logic -------------------------------------------------------

def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    rows = load_rows(args.csv)
    if not rows:
        logger.warning("No valid rows found in CSV")
        return 0

    logger.info("Loaded %d rows from %s", len(rows), args.csv)

    db = SessionLocal()
    try:
        created = 0
        updated = 0
        skipped = 0
        errors = 0
        report_rows: list[dict[str, str]] = []

        for row in rows:
            try:
                # Check for existing record by dedup key
                existing = (
                    db.query({Model})
                    .filter({Model}.{dedup_field} == row.{field_1})
                    .first()
                )

                if existing:
                    if args.update_existing:
                        if args.dry_run:
                            report_rows.append({
                                "{dedup_col}": row.{field_1},
                                "status": "dry_run_update",
                                "note": "",
                            })
                            continue

                        # Update fields
                        existing.{field_2} = row.{field_2}
                        existing.is_active = True
                        db.commit()
                        updated += 1
                        report_rows.append({
                            "{dedup_col}": row.{field_1},
                            "status": "updated",
                            "note": str(existing.id),
                        })
                    else:
                        skipped += 1
                        report_rows.append({
                            "{dedup_col}": row.{field_1},
                            "status": "skipped_existing",
                            "note": "",
                        })
                    continue

                # Create new record
                if args.dry_run:
                    report_rows.append({
                        "{dedup_col}": row.{field_1},
                        "status": "dry_run_create",
                        "note": "",
                    })
                    continue

                entity = {Model}(
                    {field_1}=row.{field_1},
                    {field_2}=row.{field_2},
                    is_active=True,
                )
                db.add(entity)
                db.commit()
                created += 1
                report_rows.append({
                    "{dedup_col}": row.{field_1},
                    "status": "created",
                    "note": str(entity.id),
                })

            except Exception as exc:
                db.rollback()
                errors += 1
                logger.error("Row %s failed: %s", row.{field_1}, exc)
                report_rows.append({
                    "{dedup_col}": row.{field_1},
                    "status": "error",
                    "note": str(exc)[:200],
                })

        # Summary
        logger.info(
            "Import complete: created=%d updated=%d skipped=%d errors=%d",
            created, updated, skipped, errors,
        )

        # Write results CSV
        if report_rows:
            report_path = f"reports/{entity_plural}_import_results.csv"
            Path(report_path).parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["{dedup_col}", "status", "note"],
                )
                writer.writeheader()
                writer.writerows(report_rows)
            logger.info("Results written to %s", report_path)

    finally:
        db.close()

    return 1 if errors > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### 4. Key patterns from existing importers

**Deduplication strategies:**
| Strategy | Use When | Example |
|----------|----------|---------|
| Exact match on unique key | SKU, email, code | `Model.sku == row.sku` |
| Case-insensitive match | Emails, names | `Model.email.ilike(row.email)` |
| Name match (fallback) | No unique key | `Model.name == row.name` |
| Composite key | Multi-field uniqueness | `Model.type == row.type, Model.code == row.code` |

**Foreign key resolution:**
```python
# Resolve role name to role_id before insert
role = db.query(Role).filter(Role.name.ilike(args.role)).first()
if not role:
    logger.error("Role not found: %s", args.role)
    return 1
```

**Password handling (user imports):**
```python
from app.services.auth_flow import hash_password
import secrets

password = args.password or secrets.token_urlsafe(16)
credential = UserCredential(
    person_id=person.id,
    username=row.email,
    password_hash=hash_password(password),
    must_change_password=True,
)
```

**Geographic data (PostGIS imports):**
```python
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

entity.geom = from_shape(Point(row.lng, row.lat), srid=4326)
```

### 5. Create a sample CSV
Create `scripts/{entity_plural}_sample.csv`:

```csv
{col1},{col2},{col3}
{sample_value_1},{sample_value_2},{sample_value_3}
```

### 6. Write tests
Create `tests/test_import_{entity_plural}.py`:

```python
import csv
import tempfile
from pathlib import Path

from scripts.import_{entity_plural} import ImportRow, load_rows


def test_load_rows_skips_empty():
    """Rows with empty dedup key are skipped."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        writer = csv.DictWriter(f, fieldnames=["{col1}", "{col2}"])
        writer.writeheader()
        writer.writerow({"{col1}": "", "{col2}": "test"})
        writer.writerow({"{col1}": "valid", "{col2}": "test"})
        f.flush()
        rows = load_rows(f.name)
    assert len(rows) == 1
    assert rows[0].{field_1} == "valid"


def test_load_rows_deduplicates():
    """Duplicate keys are removed."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        writer = csv.DictWriter(f, fieldnames=["{col1}", "{col2}"])
        writer.writeheader()
        writer.writerow({"{col1}": "SKU1", "{col2}": "Item A"})
        writer.writerow({"{col1}": "SKU1", "{col2}": "Item B"})
        f.flush()
        rows = load_rows(f.name)
    assert len(rows) == 1


def test_dry_run_does_not_write(db_session):
    """Dry run mode should not create any records."""
    pass
```

### 7. Verify
```bash
# Test CSV loading (no DB needed)
python3 -c "from scripts.import_{entity_plural} import load_rows; print(load_rows('scripts/{entity_plural}_sample.csv'))"

# Dry run
python scripts/import_{entity_plural}.py --csv scripts/{entity_plural}_sample.csv --dry-run -v

# Actual import
python scripts/import_{entity_plural}.py --csv scripts/{entity_plural}_sample.csv -v

# Check results
cat reports/{entity_plural}_import_results.csv
```

### 8. Checklist
- [ ] `--dry-run` flag prints actions without DB writes
- [ ] `--update-existing` flag updates instead of skipping duplicates
- [ ] `--verbose` flag enables DEBUG logging
- [ ] Per-row `try/except` with `db.rollback()` (one failure doesn't stop batch)
- [ ] CSV results report written to `reports/` directory
- [ ] Dedup key validated (skip empty/duplicate rows)
- [ ] Input normalization (whitespace, case) applied consistently
- [ ] Foreign keys resolved before insert (fail early if not found)
- [ ] `sys.path.insert` for running from project root
- [ ] No N+1: batch-resolve foreign keys if many distinct values
- [ ] Summary log with created/updated/skipped/errors counts
