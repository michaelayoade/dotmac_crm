#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass

from app.db import SessionLocal
from app.models.person import Person
from app.models.dispatch import TechnicianProfile


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.strip().split())


def _normalize_email(value: str) -> str:
    return _normalize_whitespace(value).lower()


@dataclass(frozen=True)
class TechRow:
    email: str
    first_name: str
    last_name: str
    region: str


def load_rows(csv_path: str) -> list[TechRow]:
    rows: list[TechRow] = []
    seen_emails: set[str] = set()
    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            email_raw = (raw.get("email") or "").strip()
            if not email_raw:
                continue
            email = _normalize_email(email_raw)
            if email in seen_emails:
                continue
            seen_emails.add(email)
            rows.append(
                TechRow(
                    email=email,
                    first_name=_normalize_whitespace(raw.get("first_name") or ""),
                    last_name=_normalize_whitespace(raw.get("last_name") or ""),
                    region=_normalize_whitespace(raw.get("region") or ""),
                )
            )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk create technician profiles.")
    parser.add_argument(
        "--csv",
        default="scripts/technicians_seed.csv",
        help="Path to CSV with first_name,last_name,email,region.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing to the database.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_rows(args.csv)
    if not rows:
        print("No rows found.")
        return 0

    db = SessionLocal()
    try:
        created = 0
        skipped_missing_person = 0
        skipped_existing = 0
        report_rows: list[dict[str, str]] = []

        for row in rows:
            person = db.query(Person).filter(Person.email.ilike(row.email)).first()
            if not person:
                skipped_missing_person += 1
                report_rows.append({"email": row.email, "status": "missing_person", "note": ""})
                continue

            existing = (
                db.query(TechnicianProfile)
                .filter(TechnicianProfile.person_id == person.id)
                .filter(TechnicianProfile.is_active.is_(True))
                .first()
            )
            if existing:
                skipped_existing += 1
                report_rows.append({"email": row.email, "status": "exists", "note": ""})
                continue

            if args.dry_run:
                print(f"DRY RUN: would add technician {row.email} ({row.region})")
                report_rows.append({"email": row.email, "status": "dry_run", "note": ""})
                continue

            tech = TechnicianProfile(
                person_id=person.id,
                title=None,
                region=row.region or None,
                is_active=True,
            )
            db.add(tech)
            db.commit()
            created += 1
            report_rows.append({"email": row.email, "status": "created", "note": row.region})

        print("Done.")
        print(f"Created technicians: {created}")
        print(f"Skipped existing: {skipped_existing}")
        print(f"Missing person: {skipped_missing_person}")

        if report_rows:
            report_path = "scripts/technician_seed_results.csv"
            with open(report_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["email", "status", "note"])
                writer.writeheader()
                writer.writerows(report_rows)
            print(f"Report: {report_path}")
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
