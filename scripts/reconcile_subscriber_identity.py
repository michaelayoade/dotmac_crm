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
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models.subscriber import Subscriber
from app.services.external_systems import SELFCARE_EXTERNAL_SYSTEM
from app.services.selfcare import stage_authoritative_subscriber_projection
from app.services.subscriber import subscriber


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the authoritative projection and any explicitly targeted Person relink",
    )
    parser.add_argument(
        "--subscriber-number",
        required=True,
        help="Limit repair to one canonical dotmac_sub subscriber number",
    )
    parser.add_argument(
        "--target-person-id",
        help="Explicit current CRM Person to link after authoritative name/UUID verification",
    )
    args = parser.parse_args()
    target_person_id = uuid.UUID(args.target_person_id) if args.target_person_id else None

    db = SessionLocal()
    try:
        target = (
            db.query(Subscriber)
            .filter(
                Subscriber.external_system == SELFCARE_EXTERNAL_SYSTEM,
                Subscriber.subscriber_number == args.subscriber_number,
                Subscriber.is_active.is_(True),
            )
            .one_or_none()
        )
        if target is None:
            parser.error("active Selfcare subscriber was not found")
        projection_result = stage_authoritative_subscriber_projection(db, target)
        result = subscriber.reconcile_external_people_links(
            db,
            external_system=SELFCARE_EXTERNAL_SYSTEM,
            clear_duplicate_metadata=False,
            dry_run=not args.apply,
            repair_legacy_merge_sources=True,
            subscriber_number=args.subscriber_number,
            target_person_id=target_person_id,
        )
        result.update(projection_result)
    finally:
        db.close()

    print(json.dumps(result, indent=2, sort_keys=True))
    print("\nAPPLIED" if args.apply else "\nDRY RUN — no database changes committed")


if __name__ == "__main__":
    main()
