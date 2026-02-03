#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

load_dotenv: Callable[..., bool] | None
try:
    from dotenv import load_dotenv as _load_dotenv
except Exception:
    load_dotenv = None
else:
    load_dotenv = _load_dotenv

from app.db import SessionLocal
from app.models.auth import AuthProvider, UserCredential
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.services.auth_flow import hash_password


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.strip().split())


def _title_case_word(word: str) -> str:
    if not word:
        return word
    hyphen_parts = []
    for part in word.split("-"):
        apostrophe_parts = [p.capitalize() for p in part.split("'")]
        hyphen_parts.append("'".join(apostrophe_parts))
    return "-".join(hyphen_parts)


def _title_case_name(value: str) -> str:
    value = _normalize_whitespace(value)
    if not value:
        return value
    return " ".join(_title_case_word(word.lower()) for word in value.split(" "))


def _normalize_email(value: str) -> str:
    return _normalize_whitespace(value).lower()


@dataclass(frozen=True)
class PersonRow:
    email: str
    first_name: str
    last_name: str


def load_rows(csv_path: str) -> list[PersonRow]:
    rows: list[PersonRow] = []
    seen_emails: set[str] = set()
    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            email_raw = (raw.get("email") or "").strip()
            first_raw = (raw.get("first_name") or "").strip()
            last_raw = (raw.get("last_name") or "").strip()
            if not email_raw:
                continue
            email = _normalize_email(email_raw)
            if email in seen_emails:
                continue
            seen_emails.add(email)
            rows.append(
                PersonRow(
                    email=email,
                    first_name=_title_case_name(first_raw),
                    last_name=_title_case_name(last_raw),
                )
            )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk create users and assign a role.")
    parser.add_argument(
        "--csv",
        default="scripts/agent_users.csv",
        help="Path to CSV with email, first_name, last_name.",
    )
    parser.add_argument(
        "--role",
        default="Agent",
        help="Role name to assign (default: Agent).",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Temporary password to set for new credentials.",
    )
    parser.add_argument(
        "--force-reset",
        action="store_true",
        help="Force password reset on next login for created/updated credentials.",
    )
    parser.add_argument(
        "--assign-role-existing",
        action="store_true",
        help="Assign role even when the person already exists.",
    )
    parser.add_argument(
        "--reset-existing-password",
        action="store_true",
        help="Reset password for existing credentials when --password is provided.",
    )
    parser.add_argument(
        "--fallback-last-name",
        default=None,
        help="Fallback last name when missing (otherwise skipped).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing to the database.",
    )
    return parser.parse_args()


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()
    args = parse_args()
    rows = load_rows(args.csv)
    if not rows:
        print("No rows found.")
        return 0

    db = SessionLocal()
    try:
        role = (
            db.query(Role)
            .filter(Role.name.ilike(args.role))
            .first()
        )
        if not role:
            print(f"Role not found: {args.role}")
            return 1

        created_people = 0
        created_credentials = 0
        updated_credentials = 0
        skipped_missing_last = 0
        role_assigned = 0
        role_already = 0
        skipped_existing = 0
        report_rows: list[dict[str, str]] = []

        for row in rows:
            first_name = row.first_name
            last_name = row.last_name
            if not last_name:
                if args.fallback_last_name:
                    last_name = _title_case_name(args.fallback_last_name)
                else:
                    skipped_missing_last += 1
                    report_rows.append(
                        {"email": row.email, "status": "skipped_missing_last", "note": ""}
                    )
                    continue

            if args.dry_run:
                print(f"DRY RUN: would create/update {row.email} ({first_name} {last_name})")
                report_rows.append({"email": row.email, "status": "dry_run", "note": ""})
                continue

            created_person = False
            person = db.query(Person).filter(Person.email.ilike(row.email)).first()
            if not person:
                person = Person(
                    first_name=first_name,
                    last_name=last_name,
                    display_name=f"{first_name} {last_name}".strip(),
                    email=row.email,
                )
                db.add(person)
                db.commit()
                db.refresh(person)
                created_people += 1
                created_person = True
            else:
                skipped_existing += 1

            existing_role = (
                db.query(PersonRole)
                .filter(PersonRole.person_id == person.id)
                .filter(PersonRole.role_id == role.id)
                .first()
            )
            if existing_role:
                role_already += 1
            elif args.assign_role_existing or created_person:
                db.add(PersonRole(person_id=person.id, role_id=role.id))
                db.commit()
                role_assigned += 1

            credential = (
                db.query(UserCredential)
                .filter(UserCredential.person_id == person.id)
                .filter(UserCredential.provider == AuthProvider.local)
                .filter(UserCredential.is_active.is_(True))
                .first()
            )
            if credential:
                if args.reset_existing_password and args.password:
                    credential.password_hash = hash_password(args.password)
                    credential.must_change_password = bool(args.force_reset)
                    credential.password_updated_at = datetime.now(timezone.utc)
                    db.commit()
                    updated_credentials += 1
            else:
                password = args.password or secrets.token_urlsafe(16)
                credential = UserCredential(
                    person_id=person.id,
                    provider=AuthProvider.local,
                    username=row.email,
                    password_hash=hash_password(password),
                    must_change_password=bool(args.force_reset),
                )
                db.add(credential)
                db.commit()
                created_credentials += 1

            report_rows.append({"email": row.email, "status": "ok", "note": ""})

        print("Done.")
        print(f"People created: {created_people}")
        print(f"Existing people: {skipped_existing}")
        print(f"Credentials created: {created_credentials}")
        print(f"Credentials updated: {updated_credentials}")
        print(f"Roles assigned: {role_assigned}")
        print(f"Roles already present: {role_already}")
        print(f"Skipped missing last name: {skipped_missing_last}")

        if report_rows:
            report_path = "scripts/agent_user_results.csv"
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
