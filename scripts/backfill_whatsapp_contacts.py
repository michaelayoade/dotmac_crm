import argparse
from datetime import datetime, timezone

from app.db import SessionLocal
from app.models.person import ChannelType as PersonChannelType, Person, PersonChannel


def _normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return None
    return f"+{digits}"


def _is_placeholder_email(email: str | None) -> bool:
    return bool(email and email.endswith("@example.invalid"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill WhatsApp contacts to ensure phone number is stored and formatted."
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of updates (0 = no limit).")
    args = parser.parse_args()

    updated_people = 0
    updated_channels = 0

    db = SessionLocal()
    try:
        q = (
            db.query(PersonChannel)
            .join(Person, PersonChannel.person_id == Person.id)
            .filter(PersonChannel.channel_type == PersonChannelType.whatsapp)
        )
        if args.limit and args.limit > 0:
            q = q.limit(args.limit)
        rows = q.all()

        for ch in rows:
            person = ch.person
            normalized = _normalize_phone(ch.address)
            if not normalized:
                continue

            person_changed = False
            channel_changed = False

            if not person.phone or not person.phone.startswith("+"):
                if person.phone != normalized:
                    person.phone = normalized
                    person_changed = True

            if ch.address != normalized:
                ch.address = normalized
                channel_changed = True

            if (not person.display_name or not person.display_name.strip()) and _is_placeholder_email(person.email):
                person.display_name = normalized
                person_changed = True

            if person_changed:
                updated_people += 1
            if channel_changed:
                updated_channels += 1

        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(
        f"whatsapp_contact_backfill updated_people={updated_people} "
        f"updated_channels={updated_channels} dry_run={args.dry_run} "
        f"completed_at={datetime.now(timezone.utc).isoformat()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
