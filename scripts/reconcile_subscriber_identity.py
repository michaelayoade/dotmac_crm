#!/usr/bin/env python3
"""Safely reconcile CRM Subscriber.person_id from authoritative external identity.

Dry-run is the default. Pass ``--apply`` only after reviewing the counts.

Usage:
    poetry run python scripts/reconcile_subscriber_identity.py --subscriber-number 100009541
    poetry run python scripts/reconcile_subscriber_identity.py --subscriber-number 100009541 --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.services.external_systems import SELFCARE_EXTERNAL_SYSTEM
from app.services.subscriber import subscriber


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Commit the proposed identity repairs")
    parser.add_argument("--subscriber-number", help="Limit repair to one canonical dotmac_sub subscriber number")
    args = parser.parse_args()
    if args.apply and not args.subscriber_number:
        parser.error("--apply requires --subscriber-number")

    db = SessionLocal()
    try:
        result = subscriber.reconcile_external_people_links(
            db,
            external_system=SELFCARE_EXTERNAL_SYSTEM,
            clear_duplicate_metadata=False,
            dry_run=not args.apply,
            repair_legacy_merge_sources=True,
            subscriber_number=args.subscriber_number,
        )
    finally:
        db.close()

    print(json.dumps(result, indent=2, sort_keys=True))
    print("\nAPPLIED" if args.apply else "\nDRY RUN — no database changes committed")


if __name__ == "__main__":
    main()
