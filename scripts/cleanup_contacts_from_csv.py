#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.person import Person, PersonChannel, ChannelType as PersonChannelType, PartyStatus
from app.services.person import People


@dataclass
class CsvRecord:
    cust_id: str
    name: str
    emails: list[str]
    phones: list[str]
    street: str
    status: str
    portal_login: str
    vat_id: str


def split_multi(value: str) -> list[str]:
    if not value:
        return []
    normalized = value.replace("/", ",")
    parts = [p.strip() for p in normalized.split(",")]
    return [p for p in parts if p]


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    return value.strip()


def truncate(value: str | None, max_len: int) -> str | None:
    if not value:
        return None
    value = value.strip()
    if len(value) <= max_len:
        return value
    return value[:max_len]


def parse_csv(path: Path) -> list[CsvRecord]:
    rows: list[CsvRecord] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t", quotechar='"')
        for row in reader:
            cust_id = (row.get("ID") or "").strip()
            if not cust_id:
                continue
            name = (row.get("Full name") or "").strip()
            emails = [normalize_email(e) for e in split_multi(row.get("Email") or "")]
            phones = [normalize_phone(p) for p in split_multi(row.get("Phone number") or "")]
            rows.append(
                CsvRecord(
                    cust_id=cust_id,
                    name=name,
                    emails=emails,
                    phones=phones,
                    street=(row.get("Street") or "").strip(),
                    status=(row.get("Status") or "").strip(),
                    portal_login=(row.get("Portal login") or "").strip(),
                    vat_id=(row.get("vat_id") or "").strip(),
                )
            )
    return rows


def build_master(rows: list[CsvRecord]) -> dict[str, CsvRecord]:
    master: dict[str, CsvRecord] = {}
    for row in rows:
        existing = master.get(row.cust_id)
        if not existing:
            master[row.cust_id] = CsvRecord(
                cust_id=row.cust_id,
                name=row.name,
                emails=list(dict.fromkeys(row.emails)),
                phones=list(dict.fromkeys(row.phones)),
                street=row.street,
                status=row.status,
                portal_login=row.portal_login,
                vat_id=row.vat_id,
            )
            continue
        if not existing.name and row.name:
            existing.name = row.name
        if not existing.street and row.street:
            existing.street = row.street
        if not existing.status and row.status:
            existing.status = row.status
        if not existing.portal_login and row.portal_login:
            existing.portal_login = row.portal_login
        if not existing.vat_id and row.vat_id:
            existing.vat_id = row.vat_id
        for e in row.emails:
            if e not in existing.emails:
                existing.emails.append(e)
        for p in row.phones:
            if p not in existing.phones:
                existing.phones.append(p)
    return master


def split_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in full_name.split() if p]
    if not parts:
        return "Unknown", "Unknown"
    if len(parts) == 1:
        return truncate(parts[0], 80) or "Unknown", "Unknown"
    first = truncate(parts[0], 80) or "Unknown"
    last = truncate(" ".join(parts[1:]), 80) or "Unknown"
    return first, last


def pick_target_by_recent(people: list[Person]) -> Person:
    return sorted(people, key=lambda p: (p.updated_at or p.created_at), reverse=True)[0]


def load_people(db: Session):
    people = db.query(Person).all()
    splynx_map: dict[str, list[Person]] = defaultdict(list)
    email_map: dict[str, list[Person]] = defaultdict(list)
    phone_map: dict[str, list[Person]] = defaultdict(list)
    for person in people:
        splynx_id = None
        if person.metadata_ and isinstance(person.metadata_, dict):
            raw = person.metadata_.get("splynx_id")
            if raw is not None:
                splynx_id = str(raw).strip()
        if splynx_id:
            splynx_map[splynx_id].append(person)
        if person.email:
            email_map[person.email.lower()].append(person)
        if person.phone:
            phone_map[person.phone].append(person)
    channels = db.query(PersonChannel).filter(PersonChannel.channel_type.in_([
        PersonChannelType.email,
        PersonChannelType.phone,
    ])).all()
    channel_email_map: dict[str, list[Person]] = defaultdict(list)
    channel_phone_map: dict[str, list[Person]] = defaultdict(list)
    for channel in channels:
        if channel.channel_type == PersonChannelType.email:
            channel_email_map[channel.address.lower()].append(channel.person)
        elif channel.channel_type == PersonChannelType.phone:
            channel_phone_map[channel.address].append(channel.person)
    return people, splynx_map, email_map, phone_map, channel_email_map, channel_phone_map


def ensure_metadata_splynx(person: Person, cust_id: str):
    metadata = person.metadata_ if isinstance(person.metadata_, dict) else {}
    metadata["splynx_id"] = cust_id
    person.metadata_ = metadata


def upsert_channels(
    db: Session,
    person: Person,
    emails: list[str],
    phones: list[str],
    reseller_emails: set[str],
):
    def upsert(channel_type: PersonChannelType, address: str, is_primary: bool, label: str | None):
        existing_channel = (
            db.query(PersonChannel)
            .filter(
                PersonChannel.person_id == person.id,
                PersonChannel.channel_type == channel_type,
                PersonChannel.address == address,
            )
            .first()
        )
        if existing_channel:
            existing_channel.is_primary = is_primary
            existing_channel.label = label
            return
        db.add(
            PersonChannel(
                person_id=person.id,
                channel_type=channel_type,
                address=address,
                is_primary=is_primary,
                label=label,
            )
        )

    primary_email = None
    for e in emails:
        if e not in reseller_emails:
            primary_email = e
            break
    primary_phone = phones[0] if phones else None

    if primary_email:
        db.query(PersonChannel).filter(
            PersonChannel.person_id == person.id,
            PersonChannel.channel_type == PersonChannelType.email,
        ).update({"is_primary": False}, synchronize_session=False)

    if primary_phone:
        db.query(PersonChannel).filter(
            PersonChannel.person_id == person.id,
            PersonChannel.channel_type == PersonChannelType.phone,
        ).update({"is_primary": False}, synchronize_session=False)

    for e in emails:
        upsert(
            PersonChannelType.email,
            e,
            is_primary=(e == primary_email),
            label="reseller" if e in reseller_emails else None,
        )

    for p in phones:
        upsert(
            PersonChannelType.phone,
            p,
            is_primary=(p == primary_phone),
            label=None,
        )


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({k for row in rows for k in row.keys()}) if rows else ["note"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            for row in rows:
                writer.writerow(row)
        else:
            writer.writerow({"note": "no rows"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--conflicts", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = parse_csv(Path(args.csv))
    master = build_master(rows)

    email_counts = Counter()
    phone_counts = Counter()
    for record in master.values():
        for e in record.emails:
            email_counts[e] += 1
        for p in record.phones:
            phone_counts[p] += 1
    reseller_emails = {e for e, c in email_counts.items() if c > 3}

    db = SessionLocal()
    people, splynx_map, email_map, phone_map, channel_email_map, channel_phone_map = load_people(db)

    report_rows = []
    conflict_rows = []
    summary = Counter()

    for cust_id, record in master.items():
        if not cust_id:
            summary["skipped_no_id"] += 1
            continue

        target = None
        duplicates = splynx_map.get(cust_id, [])
        if duplicates:
            target = pick_target_by_recent(duplicates)
            if len(duplicates) > 1:
                summary["dup_splynx_id"] += 1
                if not args.dry_run:
                    for other in duplicates:
                        if other.id == target.id:
                            continue
                        People.merge(db, other.id, target.id, merged_by_id=None)
        else:
            matched = set()
            for e in record.emails:
                if email_counts[e] == 1:
                    matched.update(email_map.get(e, []))
                    matched.update(channel_email_map.get(e, []))
            for p in record.phones:
                if phone_counts[p] == 1:
                    matched.update(phone_map.get(p, []))
                    matched.update(channel_phone_map.get(p, []))
            if len(matched) == 1:
                target = list(matched)[0]
                summary["match_by_contact"] += 1
            elif len(matched) > 1:
                summary["conflict_multi_match"] += 1
                conflict_rows.append({
                    "customer_id": cust_id,
                    "issue": "multi_match",
                    "matched_person_ids": ",".join(sorted({str(p.id) for p in matched})),
                })
                continue
            else:
                summary["create_new"] += 1
                if not args.dry_run:
                    first_name, last_name = split_name(record.name)
                    placeholder_email = f"contact-{cust_id}@placeholder.local"
                    target = Person(
                        first_name=first_name,
                        last_name=last_name,
                        display_name=truncate(record.name, 120),
                        email=placeholder_email,
                        phone=truncate(record.phones[0], 40) if record.phones else None,
                        party_status=PartyStatus.customer,
                        is_active=True,
                        address_line1=truncate(record.street, 120),
                    )
                    ensure_metadata_splynx(target, cust_id)
                    db.add(target)
                    db.flush()
                else:
                    target = None

        if not target:
            continue

        first_name, last_name = split_name(record.name)
        if not args.dry_run:
            target.first_name = first_name
            target.last_name = last_name
            target.display_name = truncate(record.name, 120)
            target.address_line1 = truncate(record.street, 120)
            target.party_status = PartyStatus.customer
            target.is_active = True
            ensure_metadata_splynx(target, cust_id)

            if not target.email:
                target.email = f"contact-{cust_id}@placeholder.local"
            if record.phones:
                target.phone = truncate(record.phones[0], 40)

            upsert_channels(db, target, record.emails, record.phones, reseller_emails)

        if record.emails:
            res_emails = [e for e in record.emails if e in reseller_emails]
            if res_emails:
                report_rows.append({
                    "customer_id": cust_id,
                    "name": record.name,
                    "reseller_emails": ",".join(res_emails),
                    "non_reseller_emails": ",".join([e for e in record.emails if e not in reseller_emails]),
                    "phones": ",".join(record.phones),
                })

    if not args.dry_run:
        db.commit()
    db.close()

    write_csv(Path(args.report), report_rows)
    write_csv(Path(args.conflicts), conflict_rows)

    print("summary:")
    for k, v in summary.items():
        print(f"{k}: {v}")
    print(f"report: {args.report}")
    print(f"conflicts: {args.conflicts}")
    print(f"reseller_emails: {len(reseller_emails)}")


if __name__ == "__main__":
    main()
