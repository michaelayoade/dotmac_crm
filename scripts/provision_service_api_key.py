#!/usr/bin/env python3
"""Provision a service ApiKey for a machine principal (e.g. dotmac_sub sync).

The key is linked to an existing ``Person`` (looked up by email), so its
authority is exactly that person's RBAC — accepting the key authenticates the
caller, RBAC still authorizes each request (see ``_resolve_service_api_key`` in
``app/services/auth_dependencies.py``).

Typical use — issue a key for the self-care sync account dotmac_sub logs in as
today, so sub can drop its username/password staff login for ``X-API-Key``:

    poetry run python scripts/provision_service_api_key.py \
        --email selfcare-sync@dotmac.io --label "dotmac_sub self-care sync"

The raw key is printed ONCE and never stored in recoverable form — capture it
into the sub deployment secret (``CRM_SERVICE_TOKEN``) immediately. Pass
``--revoke-existing`` to retire the account's prior active keys on rotation.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models.auth import ApiKey
from app.models.person import Person
from app.schemas.auth import ApiKeyGenerateRequest
from app.services.auth import ApiKeys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--email",
        default="selfcare-sync@dotmac.io",
        help="Email of the existing Person to link the key to.",
    )
    parser.add_argument(
        "--label",
        default="dotmac_sub self-care sync",
        help="Human-readable label stored on the key.",
    )
    parser.add_argument(
        "--expires-days",
        type=int,
        default=None,
        help="Optional expiry in days from now (default: no expiry).",
    )
    parser.add_argument(
        "--revoke-existing",
        action="store_true",
        help="Revoke the person's other active keys first (rotation).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    email = args.email.strip().lower()

    with SessionLocal() as db:
        person = db.query(Person).filter(Person.email == email).first()
        if person is None:
            print(f"ERROR: no Person found with email {email!r}", file=sys.stderr)
            return 1

        if args.revoke_existing:
            existing = (
                db.query(ApiKey)
                .filter(ApiKey.person_id == person.id)
                .filter(ApiKey.is_active.is_(True))
                .filter(ApiKey.revoked_at.is_(None))
                .all()
            )
            for key in existing:
                key.is_active = False
                key.revoked_at = datetime.now(UTC)
            if existing:
                db.commit()
                print(f"Revoked {len(existing)} existing active key(s) for {email}.")

        expires_at = datetime.now(UTC) + timedelta(days=args.expires_days) if args.expires_days else None
        payload = ApiKeyGenerateRequest(
            person_id=person.id,
            label=args.label,
            expires_at=expires_at,
        )
        api_key, raw_key = ApiKeys.generate(db, payload)

        # Capture the values to display while the instances are still bound to
        # the session — reading them after the ``with`` block closes would raise
        # DetachedInstanceError (SessionLocal expires attributes on commit).
        person_id = person.id
        key_id = api_key.id
        key_label = api_key.label
        key_expires = api_key.expires_at

    print("Service ApiKey provisioned.")
    print(f"  person : {email} ({person_id})")
    print(f"  key id : {key_id}")
    print(f"  label  : {key_label}")
    print(f"  expires: {key_expires or 'never'}")
    print()
    print("Raw key (shown ONCE — store it in the sub CRM_SERVICE_TOKEN secret):")
    print(f"  {raw_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
