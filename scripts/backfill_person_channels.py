#!/usr/bin/env python3
# ruff: noqa: T201, E402
"""Backfill PersonChannel rows for Persons who have email/phone but no matching channel.

Usage:
    python scripts/backfill_person_channels.py --dry-run
    python scripts/backfill_person_channels.py
    python scripts/backfill_person_channels.py --detect-dupes
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

# Bootstrap the app environment
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import func

from app.db import SessionLocal
from app.models.person import ChannelType, Person, PersonChannel


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return f"+{digits}" if digits else None


def _has_channel(db, person_id, channel_type: ChannelType, address: str) -> bool:
    return (
        db.query(PersonChannel)
        .filter(
            PersonChannel.person_id == person_id,
            PersonChannel.channel_type == channel_type,
            func.lower(PersonChannel.address) == address.lower(),
        )
        .first()
        is not None
    )


PLACEHOLDER_DOMAINS = {"example.invalid", "widget.local", "placeholder.local"}


def _is_placeholder(email: str) -> bool:
    parts = email.rsplit("@", 1)
    return len(parts) == 2 and parts[1] in PLACEHOLDER_DOMAINS


def backfill(dry_run: bool = True) -> dict:
    db = SessionLocal()
    stats = {"email_created": 0, "phone_created": 0, "skipped": 0, "errors": 0}

    try:
        persons = db.query(Person).filter(Person.is_active.is_(True)).all()
        print(f"Found {len(persons)} active persons")

        for person in persons:
            # Backfill email channel
            if (
                person.email
                and not _is_placeholder(person.email)
                and not _has_channel(db, person.id, ChannelType.email, person.email)
            ):
                if dry_run:
                    print(f"  [DRY RUN] Would create email channel: {person.email} for {person.id}")
                else:
                    db.add(
                        PersonChannel(
                            person_id=person.id,
                            channel_type=ChannelType.email,
                            address=person.email.strip().lower(),
                            is_primary=True,
                        )
                    )
                stats["email_created"] += 1

            # Backfill phone channel
            if person.phone:
                normalized = _normalize_phone(person.phone)
                if normalized and not _has_channel(db, person.id, ChannelType.phone, normalized):
                    if dry_run:
                        print(f"  [DRY RUN] Would create phone channel: {normalized} for {person.id}")
                    else:
                        db.add(
                            PersonChannel(
                                person_id=person.id,
                                channel_type=ChannelType.phone,
                                address=normalized,
                                is_primary=False,
                            )
                        )
                    stats["phone_created"] += 1

        if not dry_run:
            db.commit()
            print("Committed changes.")
        else:
            print("\n[DRY RUN] No changes made.")

    except Exception as e:
        db.rollback()
        stats["errors"] += 1
        print(f"ERROR: {e}")
        raise
    finally:
        db.close()

    return stats


def detect_dupes() -> None:
    """Find potential duplicate persons (same phone across multiple records)."""
    db = SessionLocal()
    try:
        persons = (
            db.query(Person)
            .filter(Person.is_active.is_(True), Person.phone.isnot(None), Person.phone != "")
            .all()
        )

        phone_to_persons: dict[str, list] = defaultdict(list)
        for person in persons:
            norm = _normalize_phone(person.phone)
            if norm:
                phone_to_persons[norm].append(person)

        dupes = {phone: ps for phone, ps in phone_to_persons.items() if len(ps) > 1}

        if not dupes:
            print("No duplicate phone numbers found.")
            return

        print(f"Found {len(dupes)} phone numbers shared across multiple persons:")
        writer = csv.writer(sys.stdout)
        writer.writerow(["phone", "person_id", "email", "display_name", "party_status", "created_at"])
        for phone, ps in sorted(dupes.items()):
            for p in ps:
                writer.writerow([
                    phone,
                    str(p.id),
                    p.email,
                    p.display_name or "",
                    p.party_status.value if p.party_status else "",
                    p.created_at.isoformat() if p.created_at else "",
                ])
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill PersonChannel rows")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without committing")
    parser.add_argument("--detect-dupes", action="store_true", help="Output CSV of potential duplicate persons")
    args = parser.parse_args()

    if args.detect_dupes:
        detect_dupes()
        return

    stats = backfill(dry_run=args.dry_run)
    print(f"\nStats: {stats}")


if __name__ == "__main__":
    main()
