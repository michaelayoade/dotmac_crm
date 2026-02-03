#!/usr/bin/env python3
"""Bulk import inventory items from CSV file.

CSV format:
    sku,name,description,unit

Example:
    SKU001,Fiber Optic Cable (100m),Single-mode fiber optic cable roll,Roll
    SKU002,RJ45 Connector,Cat6 RJ45 connector pack of 100,Pack
    SKU003,ONT Device,Huawei ONT device,Each

Usage:
    python scripts/bulk_import_inventory.py --csv scripts/inventory_items.csv
    python scripts/bulk_import_inventory.py --csv scripts/inventory_items.csv --dry-run
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass

from app.db import SessionLocal
from app.models.inventory import InventoryItem


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.strip().split())


@dataclass(frozen=True)
class ItemRow:
    sku: str
    name: str
    description: str
    unit: str


def load_rows(csv_path: str) -> list[ItemRow]:
    """Load and validate rows from CSV file."""
    rows: list[ItemRow] = []
    seen_skus: set[str] = set()
    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            name_raw = (raw.get("name") or "").strip()
            if not name_raw:
                continue
            sku = _normalize_whitespace(raw.get("sku") or "")
            # Skip duplicate SKUs if provided
            if sku and sku in seen_skus:
                continue
            if sku:
                seen_skus.add(sku)
            rows.append(
                ItemRow(
                    sku=sku,
                    name=_normalize_whitespace(name_raw),
                    description=_normalize_whitespace(raw.get("description") or ""),
                    unit=_normalize_whitespace(raw.get("unit") or "Each"),
                )
            )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk import inventory items from CSV.")
    parser.add_argument(
        "--csv",
        default="scripts/inventory_items.csv",
        help="Path to CSV with sku,name,description,unit columns.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing to the database.",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update existing items (matched by SKU) instead of skipping.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_rows(args.csv)
    if not rows:
        print("No rows found in CSV file.")
        return 0

    db = SessionLocal()
    try:
        created = 0
        updated = 0
        skipped_existing = 0
        skipped_no_sku_match = 0
        report_rows: list[dict[str, str]] = []

        for row in rows:
            # Check if item exists by SKU
            existing = None
            if row.sku:
                existing = (
                    db.query(InventoryItem)
                    .filter(InventoryItem.sku == row.sku)
                    .first()
                )

            if existing:
                if args.update_existing:
                    if args.dry_run:
                        print(f"DRY RUN: would update '{row.name}' (SKU: {row.sku})")
                        report_rows.append({
                            "sku": row.sku,
                            "name": row.name,
                            "status": "dry_run_update",
                            "note": "",
                        })
                    else:
                        existing.name = row.name
                        existing.description = row.description or None
                        existing.unit = row.unit or "Each"
                        existing.is_active = True
                        db.commit()
                        updated += 1
                        report_rows.append({
                            "sku": row.sku,
                            "name": row.name,
                            "status": "updated",
                            "note": "",
                        })
                else:
                    skipped_existing += 1
                    report_rows.append({
                        "sku": row.sku,
                        "name": row.name,
                        "status": "skipped_existing",
                        "note": "",
                    })
                continue

            # Check if item exists by name (for items without SKU)
            if not row.sku:
                name_match = (
                    db.query(InventoryItem)
                    .filter(InventoryItem.name == row.name)
                    .first()
                )
                if name_match:
                    skipped_no_sku_match += 1
                    report_rows.append({
                        "sku": row.sku or "(none)",
                        "name": row.name,
                        "status": "skipped_name_exists",
                        "note": "",
                    })
                    continue

            # Create new item
            if args.dry_run:
                print(f"DRY RUN: would create '{row.name}' (SKU: {row.sku or '(none)'})")
                report_rows.append({
                    "sku": row.sku or "(none)",
                    "name": row.name,
                    "status": "dry_run_create",
                    "note": "",
                })
                continue

            item = InventoryItem(
                sku=row.sku or None,
                name=row.name,
                description=row.description or None,
                unit=row.unit or "Each",
                is_active=True,
            )
            db.add(item)
            db.commit()
            created += 1
            report_rows.append({
                "sku": row.sku or "(none)",
                "name": row.name,
                "status": "created",
                "note": str(item.id),
            })

        print("\n=== Import Summary ===")
        print(f"Created: {created}")
        print(f"Updated: {updated}")
        print(f"Skipped (existing SKU): {skipped_existing}")
        print(f"Skipped (name match): {skipped_no_sku_match}")
        print(f"Total processed: {len(rows)}")

        if report_rows:
            report_path = "scripts/inventory_import_results.csv"
            with open(report_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["sku", "name", "status", "note"])
                writer.writeheader()
                writer.writerows(report_rows)
            print(f"\nReport saved to: {report_path}")

    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
