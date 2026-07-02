#!/usr/bin/env python3
"""Backfill people.metadata.selfcare_id from each person's linked selfcare
subscriber, converging legacy splynx-only identities onto selfcare.

Dry-run by default — pass --apply to write. Preserves the existing splynx_id;
only people with a resolvable linked selfcare subscriber are touched.

Usage:
    poetry run python scripts/backfill_person_selfcare_id.py           # dry run
    poetry run python scripts/backfill_person_selfcare_id.py --apply   # execute
"""

import argparse
import json

from app.db import SessionLocal
from app.services.splynx_convergence import backfill_person_selfcare_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write selfcare_id (default: dry run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = backfill_person_selfcare_id(db, apply=args.apply)
    finally:
        db.close()

    print(json.dumps(result, indent=2, sort_keys=True))
    if not args.apply:
        print(f"\nDRY RUN — {result['candidates']} person(s) would get selfcare_id. Re-run with --apply.")
    else:
        print(f"\nAPPLIED — backfilled selfcare_id on {result['backfilled']} person(s).")


if __name__ == "__main__":
    main()
