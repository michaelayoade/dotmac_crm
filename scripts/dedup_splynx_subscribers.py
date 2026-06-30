#!/usr/bin/env python3
"""Soft-delete CRM subscriber rows still keyed under legacy 'splynx' that
duplicate a canonical 'selfcare' row for the same subscriber.

Dry-run by default — pass --apply to soft-delete (reversible: is_active=False).
Run ONLY after the dotmac_sub push fix has deployed, otherwise sub recreates
the duplicates on the next push.

Usage:
    poetry run python scripts/dedup_splynx_subscribers.py           # dry run
    poetry run python scripts/dedup_splynx_subscribers.py --apply   # execute
"""

import argparse
import json

from app.db import SessionLocal
from app.services.splynx_convergence import dedupe_splynx_duplicates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Soft-delete duplicates (default: dry run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = dedupe_splynx_duplicates(db, apply=args.apply)
    finally:
        db.close()

    print(json.dumps(result, indent=2, sort_keys=True))
    if not args.apply:
        print(
            f"\nDRY RUN — {result['duplicates']} splynx duplicate(s) would be soft-deleted; "
            f"{result['no_twin']} have no selfcare twin (left for manual review). "
            "Re-run with --apply to execute."
        )
    else:
        print(f"\nAPPLIED — soft-deleted {result['soft_deleted']} duplicate splynx row(s).")


if __name__ == "__main__":
    main()
