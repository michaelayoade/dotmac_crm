#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass

import json
import http.cookies
import urllib.parse
import urllib.request


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


class DotmacError(Exception):
    def __init__(self, status: int, reason: str, body: str):
        self.status = status
        self.reason = reason
        self.body = body
        super().__init__(f"HTTP {status} {reason}: {body}")


class DotmacClient:
    def __init__(self, base_url: str, token: str, timeout: float = 20.0, debug: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.debug = debug
        self.cookies: dict[str, str] = {}
        self.csrf_token: str | None = None
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def close(self) -> None:
        return None

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        payload: dict | None = None,
        parse_json: bool = True,
        content_type: str | None = None,
        extra_headers: dict | None = None,
    ):
        url = f"{self.base_url}{path}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"
        if self.debug:
            print(f"[DEBUG] {method} {url}")
        data = None
        headers = dict(self.headers)
        if content_type:
            headers["Content-Type"] = content_type
        if self.cookies:
            cookie_value = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            headers["Cookie"] = cookie_value
        if extra_headers:
            headers.update(extra_headers)
        if payload is not None:
            if headers.get("Content-Type") == "application/x-www-form-urlencoded":
                data = urllib.parse.urlencode(payload).encode("utf-8")
            else:
                data = json.dumps(payload).encode("utf-8")
            if self.debug:
                print(f"[DEBUG] payload={payload}")
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                self._store_cookies(resp)
                if self.debug:
                    print(f"[DEBUG] status={resp.status} body={body}")
                if not body:
                    return {} if parse_json else ""
                if parse_json:
                    return json.loads(body)
                return body
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            self._store_cookies(exc)
            if self.debug:
                print(f"[DEBUG] status={exc.code} body={body}")
            msg = body or str(exc)
            raise DotmacError(exc.code, exc.reason, msg) from exc

    def _store_cookies(self, response) -> None:
        set_cookies = response.headers.get_all("Set-Cookie") if response.headers else []
        if not set_cookies:
            return
        for header in set_cookies:
            cookie = http.cookies.SimpleCookie()
            cookie.load(header)
            for key, morsel in cookie.items():
                self.cookies[key] = morsel.value
                if key == "csrf_token":
                    self.csrf_token = morsel.value

    def list_people_by_email(self, email: str) -> list[dict]:
        data = self._request("GET", "/api/v1/people", params={"email": email, "limit": 200})
        return data.get("items", [])

    def create_person(self, payload: dict) -> dict:
        return self._request("POST", "/api/v1/people", payload=payload)

    def create_system_user(self, payload: dict) -> str:
        if not self.csrf_token:
            # Trigger CSRF cookie issuance
            self._request("GET", "/admin/system/users", parse_json=False)
        headers = {}
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        return self._request(
            "POST",
            "/admin/system/users",
            payload=payload,
            parse_json=False,
            content_type="application/x-www-form-urlencoded",
            extra_headers=headers,
        )

    def list_roles(self) -> list[dict]:
        data = self._request("GET", "/api/v1/rbac/roles", params={"limit": 200})
        return data.get("items", [])

    def list_person_roles(self, person_id: str, role_id: str) -> list[dict]:
        data = self._request(
            "GET",
            "/api/v1/rbac/person-roles",
            params={"person_id": person_id, "role_id": role_id, "limit": 1},
        )
        return data.get("items", [])

    def assign_person_role(self, person_id: str, role_id: str) -> dict:
        return self._request(
            "POST",
            "/api/v1/rbac/person-roles",
            payload={"person_id": person_id, "role_id": role_id},
        )


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


def resolve_role_id(client: DotmacClient, role_name: str) -> str:
    roles = client.list_roles()
    for role in roles:
        name = str(role.get("name", ""))
        if name.strip().lower() == role_name.strip().lower():
            return str(role.get("id"))
    raise SystemExit(f"Role not found: {role_name}")


def ensure_role(
    client: DotmacClient,
    person_id: str,
    role_id: str,
    dry_run: bool,
) -> None:
    existing_links = client.list_person_roles(person_id, role_id)
    if existing_links:
        return
    if dry_run:
        return
    client.assign_person_role(person_id, role_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk create help desk users.")
    parser.add_argument(
        "--csv",
        default=os.environ.get("DOTMAC_USERS_CSV", "scripts/helpdesk_users.csv"),
        help="Path to CSV with email, first_name, last_name.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DOTMAC_BASE_URL", "https://crm.dotmac.io"),
        help="CRM base URL (default: https://crm.dotmac.io)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("DOTMAC_ACCESS_TOKEN"),
        help="Bearer access token (or set DOTMAC_ACCESS_TOKEN).",
    )
    parser.add_argument(
        "--role",
        default=os.environ.get("DOTMAC_DEFAULT_ROLE", "Help desk"),
        help="Role name to assign (default: Help desk).",
    )
    parser.add_argument(
        "--assign-role-existing",
        action="store_true",
        help="Also assign role to users that already exist.",
    )
    parser.add_argument(
        "--create-via-admin",
        action="store_true",
        help="Create users via /admin/system/users (adds credentials for UI list).",
    )
    parser.add_argument(
        "--report",
        default=os.environ.get("DOTMAC_USERS_REPORT", "scripts/helpdesk_user_results.csv"),
        help="Write results to CSV.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without creating users or roles.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print request/response details for troubleshooting.",
    )
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("Missing access token. Set DOTMAC_ACCESS_TOKEN or pass --token.")

    rows = load_rows(args.csv)
    if not rows:
        print("No rows found.")
        return 0

    client = DotmacClient(args.base_url, args.token, debug=args.debug)
    try:
        role_id = resolve_role_id(client, args.role)
        created = 0
        skipped_existing = 0
        skipped_missing_last = 0
        role_assigned = 0
        failures = 0
        report_rows: list[dict[str, str]] = []

        for row in rows:
            if not row.last_name:
                skipped_missing_last += 1
                report_rows.append(
                    {"email": row.email, "status": "skipped_missing_last", "note": ""}
                )
                continue

            payload = {
                "first_name": row.first_name,
                "last_name": row.last_name,
                "email": row.email,
                "display_name": f"{row.first_name} {row.last_name}",
            }

            if args.dry_run:
                print(f"DRY RUN: would create {row.email} ({row.first_name} {row.last_name})")
                created += 1
                report_rows.append({"email": row.email, "status": "dry_run", "note": ""})
                continue

            if args.create_via_admin:
                form_payload = {
                    "first_name": row.first_name,
                    "last_name": row.last_name,
                    "email": row.email,
                    "role_id": role_id,
                }
                try:
                    body = client.create_system_user(form_payload)
                except DotmacError as exc:
                    print(f"Failed to create {row.email}: {exc}")
                    failures += 1
                    report_rows.append({"email": row.email, "status": "error", "note": str(exc)})
                    continue

                body_lower = body.lower()
                if "user already exists" in body_lower or "username already in use" in body_lower:
                    print(f"Already exists, skipped create for {row.email}")
                    skipped_existing += 1
                    report_rows.append({"email": row.email, "status": "exists", "note": ""})
                    continue
                if "user created" in body_lower or "invitation sent" in body_lower:
                    created += 1
                    role_assigned += 1
                    report_rows.append({"email": row.email, "status": "created", "note": ""})
                    continue
                if "error" in body_lower:
                    print(f"Create returned error for {row.email}")
                    failures += 1
                    report_rows.append({"email": row.email, "status": "error", "note": "unknown error response"})
                    continue

                created += 1
                role_assigned += 1
                report_rows.append({"email": row.email, "status": "created", "note": ""})
                continue

            existing_people = []
            lookup_failed = False
            try:
                existing_people = client.list_people_by_email(row.email)
            except DotmacError as exc:
                print(f"Failed to check {row.email}: {exc}")
                failures += 1
                lookup_failed = True
            matched = None
            for person in existing_people:
                if str(person.get("email", "")).lower() == row.email:
                    matched = person
                    break

            if matched:
                skipped_existing += 1
                report_rows.append({"email": row.email, "status": "exists", "note": ""})
                if args.assign_role_existing:
                    try:
                        ensure_role(client, str(matched.get("id")), role_id, args.dry_run)
                        role_assigned += 1
                    except DotmacError as exc:
                        print(f"Failed to assign role for {row.email}: {exc}")
                        failures += 1
                continue

            try:
                created_person = client.create_person(payload)
                created += 1
                report_rows.append({"email": row.email, "status": "created", "note": ""})
            except DotmacError as exc:
                if exc.status == 409:
                    print(f"Already exists, skipped create for {row.email}")
                    skipped_existing += 1
                    report_rows.append({"email": row.email, "status": "exists", "note": ""})
                    continue
                if lookup_failed and exc.status in {400, 422}:
                    print(f"Create rejected for {row.email}: {exc}")
                    failures += 1
                    report_rows.append({"email": row.email, "status": "error", "note": str(exc)})
                    continue
                print(f"Failed to create {row.email}: {exc}")
                failures += 1
                report_rows.append({"email": row.email, "status": "error", "note": str(exc)})
                continue
            try:
                ensure_role(client, str(created_person.get("id")), role_id, args.dry_run)
                role_assigned += 1
            except DotmacError as exc:
                print(f"Failed to assign role for {row.email}: {exc}")
                failures += 1

        print("Done.")
        print(f"Created: {created}")
        print(f"Skipped existing: {skipped_existing}")
        print(f"Skipped missing last name: {skipped_missing_last}")
        print(f"Role assigned: {role_assigned}")
        print(f"Failures: {failures}")
        if report_rows:
            with open(args.report, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["email", "status", "note"])
                writer.writeheader()
                writer.writerows(report_rows)
            print(f"Report: {args.report}")
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
