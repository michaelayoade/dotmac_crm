#!/usr/bin/env python3
"""Report splynx -> selfcare keying convergence progress (read-only).

The selfcare bulk sync re-keys legacy splynx subscriber rows onto selfcare over
time. Run this against the CRM database to see how far that has progressed and
whether it is safe to retire the legacy splynx code paths.

Usage:
    poetry run python scripts/splynx_convergence_status.py
"""

import json

from app.db import SessionLocal
from app.services.splynx_convergence import convergence_status


def main() -> None:
    db = SessionLocal()
    try:
        status = convergence_status(db)
    finally:
        db.close()

    print(json.dumps(status, indent=2, sort_keys=True))
    if status["converged"]:
        print("\nCONVERGED — no splynx-keyed subscribers or splynx-only identities remain.")
        print("Safe to begin retiring the legacy splynx code paths.")
    else:
        print(
            f"\nIN PROGRESS — {status['subscribers_remaining_splynx']} subscriber(s) still keyed 'splynx', "
            f"{status['people_splynx_id_without_selfcare_id']} person(s) with splynx_id but no selfcare_id."
        )
        print("Ensure the selfcare subscriber sync is enabled and let it run; re-check.")


if __name__ == "__main__":
    main()
