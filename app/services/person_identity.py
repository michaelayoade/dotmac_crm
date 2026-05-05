"""Unified person identity resolution.

Single entry point for all person lookup-or-create operations across the app.
Every inbound channel (WhatsApp, email, widget, Meta webhooks, ERP import,
Chatwoot import) should call ``resolve_person()`` instead of implementing
its own lookup logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crm.enums import ChannelType as CrmChannelType
from app.models.person import ChannelType, PartyStatus, Person, PersonChannel

logger = logging.getLogger(__name__)


def _is_meta_placeholder_name(value: str | None) -> bool:
    if not value:
        return False
    candidate = " ".join(value.strip().split())
    if not candidate:
        return False
    return candidate.startswith("Facebook User") or candidate.startswith("Instagram User")


# ---------------------------------------------------------------------------
# Inline normalizers — intentionally duplicated from crm.inbox.normalizers
# to avoid circular imports (person_identity → crm → crm.inbox → contacts → person_identity).
# ---------------------------------------------------------------------------


def _normalize_email_address(address: str | None) -> str | None:
    if not address:
        return None
    candidate = address.strip().lower()
    return candidate or None


def _normalize_phone_address(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return None
    return f"+{digits}"


# Placeholder email domains used by various importers/widgets
_PLACEHOLDER_DOMAINS = frozenset(
    {
        "example.invalid",
        "widget.local",
        "placeholder.local",
        "reseller.dotmac.ng",
    }
)

# Channel types that represent phone-based identifiers
_PHONE_CHANNEL_TYPES = frozenset(
    {
        ChannelType.phone,
        ChannelType.sms,
        ChannelType.whatsapp,
    }
)


def is_placeholder_email(email: str | None) -> bool:
    """Return True if *email* is a system-generated placeholder."""
    if not email:
        return False
    parts = email.strip().lower().rsplit("@", 1)
    if len(parts) != 2:
        return False
    return parts[1] in _PLACEHOLDER_DOMAINS


def _normalize_channel_address(channel_type: CrmChannelType | ChannelType, address: str | None) -> str | None:
    """Normalize an address for the given channel type."""
    if not address:
        return None
    ct_value = channel_type.value if hasattr(channel_type, "value") else str(channel_type)
    if ct_value == "email":
        return _normalize_email_address(address)
    if ct_value in ("whatsapp", "phone", "sms"):
        return _normalize_phone_address(address)
    return address.strip() or None


def _email_from_address(channel_type_value: str, address: str) -> str:
    """Generate a placeholder email for non-email channels."""
    safe = "".join(ch if ch.isalnum() else "-" for ch in address)
    return f"{channel_type_value}-{safe}@example.invalid"


@dataclass
class ResolvedIdentity:
    """Result of ``resolve_person()``."""

    person: Person
    channel: PersonChannel
    created: bool  # True if person was newly created
    channel_backfilled: bool  # True if channel was backfilled on existing person


def ensure_person_channel(
    db: Session,
    person: Person,
    channel_type: ChannelType,
    address: str,
) -> tuple[PersonChannel, bool]:
    """Ensure *person* has a channel record for (*channel_type*, *address*).

    Returns ``(channel, created)`` — *created* is True when a new row was inserted.
    """
    normalized = _normalize_channel_address(channel_type, address) or address.strip()
    existing = (
        db.query(PersonChannel)
        .filter(
            PersonChannel.person_id == person.id,
            PersonChannel.channel_type == channel_type,
            or_(
                PersonChannel.address == normalized,
                PersonChannel.address == address.strip(),
            ),
        )
        .first()
    )
    if existing:
        return existing, False

    has_primary = (
        db.query(PersonChannel)
        .filter(
            PersonChannel.person_id == person.id,
            PersonChannel.channel_type == channel_type,
            PersonChannel.is_primary.is_(True),
        )
        .first()
    )
    channel = PersonChannel(
        person_id=person.id,
        channel_type=channel_type,
        address=normalized,
        is_primary=has_primary is None,
    )
    db.add(channel)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(PersonChannel)
            .filter(
                PersonChannel.person_id == person.id,
                PersonChannel.channel_type == channel_type,
                PersonChannel.address == normalized,
            )
            .first()
        )
        if existing:
            return existing, False
        raise
    return channel, True


def _enrich_person(
    db: Session,
    person: Person,
    *,
    email: str | None = None,
    phone: str | None = None,
    display_name: str | None = None,
) -> bool:
    """Fill in missing fields on *person*. Returns True if any field changed."""
    changed = False

    # Replace placeholder email with real one
    if email:
        norm_email = _normalize_email_address(email)
        if (
            norm_email
            and not is_placeholder_email(norm_email)
            and (not person.email or is_placeholder_email(person.email))
        ):
            person.email = norm_email
            changed = True

    # Fill in phone if missing
    if phone:
        norm_phone = _normalize_phone_address(phone)
        if norm_phone and not person.phone:
            person.phone = norm_phone
            changed = True

    # Fill in display_name if missing, or replace known Meta placeholders with a real profile name.
    if display_name and (not person.display_name or _is_meta_placeholder_name(person.display_name)):
        person.display_name = display_name
        changed = True

    if changed:
        db.flush()
    return changed


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def _find_by_channel(
    db: Session,
    channel_type: ChannelType,
    normalized: str,
    raw: str,
) -> tuple[Person | None, PersonChannel | None]:
    """Step 1: exact PersonChannel match."""
    channel = (
        db.query(PersonChannel)
        .filter(
            PersonChannel.channel_type == channel_type,
            or_(
                PersonChannel.address == normalized,
                PersonChannel.address == raw,
            ),
        )
        .first()
    )
    if channel:
        return channel.person, channel
    return None, None


def _find_by_cross_type_channel(
    db: Session,
    channel_type: ChannelType,
    normalized: str,
    raw: str,
) -> tuple[Person | None, PersonChannel | None]:
    """Step 2: cross-type channel match (e.g. whatsapp addr → phone/sms channel)."""
    if channel_type not in _PHONE_CHANNEL_TYPES:
        return None, None
    other_types = _PHONE_CHANNEL_TYPES - {channel_type}
    channel = (
        db.query(PersonChannel)
        .filter(
            PersonChannel.channel_type.in_(other_types),
            or_(
                PersonChannel.address == normalized,
                PersonChannel.address == raw,
            ),
        )
        .first()
    )
    if channel:
        return channel.person, channel
    return None, None


def _find_by_person_email(db: Session, email: str | None) -> Person | None:
    """Step 3: Person.email match."""
    if not email:
        return None
    norm = _normalize_email_address(email)
    if not norm or is_placeholder_email(norm):
        return None
    return db.query(Person).filter(func.lower(Person.email) == norm).first()


def _find_by_person_phone(db: Session, phone: str | None) -> Person | None:
    """Step 4: Person.phone match."""
    if not phone:
        return None
    norm = _normalize_phone_address(phone)
    if not norm:
        return None
    return (
        db.query(Person)
        .filter(
            or_(
                Person.phone == norm,
                Person.phone == phone.strip(),
            )
        )
        .first()
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def resolve_person(
    db: Session,
    *,
    channel_type: CrmChannelType | ChannelType,
    address: str,
    display_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> ResolvedIdentity:
    """Resolve or create a Person + PersonChannel for the given identifiers.

    Lookup order:
    1. PersonChannel exact match (channel_type + normalized address)
    2. PersonChannel cross-type match (whatsapp ↔ phone/sms)
    3. Person.email match (if email channel or email hint provided)
    4. Person.phone match (if phone channel or phone hint provided)
    5. Cross-identifier hints (email hint → Person.email, phone hint → Person.phone)
    6. Create new Person + PersonChannel

    After every match: backfill missing PersonChannel, enrich placeholder data.
    """
    # Coerce CrmChannelType → ChannelType if needed
    if isinstance(channel_type, CrmChannelType):
        person_channel_type = ChannelType(channel_type.value)
    else:
        person_channel_type = channel_type

    normalized = _normalize_channel_address(channel_type, address) or address.strip()
    raw = address.strip()

    # --- Step 1: exact PersonChannel match ---
    person, channel = _find_by_channel(db, person_channel_type, normalized, raw)
    if person and channel:
        _enrich_person(db, person, email=email, phone=phone, display_name=display_name)
        return ResolvedIdentity(person=person, channel=channel, created=False, channel_backfilled=False)

    # --- Step 2: cross-type channel match ---
    person, _ = _find_by_cross_type_channel(db, person_channel_type, normalized, raw)
    if person:
        ch, backfilled = ensure_person_channel(db, person, person_channel_type, normalized)
        _enrich_person(db, person, email=email, phone=phone, display_name=display_name)
        return ResolvedIdentity(person=person, channel=ch, created=False, channel_backfilled=backfilled)

    # --- Step 3: Person.email match ---
    email_for_lookup = normalized if person_channel_type == ChannelType.email else email
    person = _find_by_person_email(db, email_for_lookup)
    if person:
        ch, backfilled = ensure_person_channel(db, person, person_channel_type, normalized)
        _enrich_person(db, person, email=email, phone=phone, display_name=display_name)
        return ResolvedIdentity(person=person, channel=ch, created=False, channel_backfilled=backfilled)

    # --- Step 4: Person.phone match ---
    phone_for_lookup = normalized if person_channel_type in _PHONE_CHANNEL_TYPES else phone
    person = _find_by_person_phone(db, phone_for_lookup)
    if person:
        ch, backfilled = ensure_person_channel(db, person, person_channel_type, normalized)
        _enrich_person(db, person, email=email, phone=phone, display_name=display_name)
        return ResolvedIdentity(person=person, channel=ch, created=False, channel_backfilled=backfilled)

    # --- Step 5: cross-identifier hints ---
    if email and person_channel_type != ChannelType.email:
        person = _find_by_person_email(db, email)
        if person:
            ch, backfilled = ensure_person_channel(db, person, person_channel_type, normalized)
            _enrich_person(db, person, email=email, phone=phone, display_name=display_name)
            return ResolvedIdentity(person=person, channel=ch, created=False, channel_backfilled=backfilled)

    if phone and person_channel_type not in _PHONE_CHANNEL_TYPES:
        person = _find_by_person_phone(db, phone)
        if person:
            ch, backfilled = ensure_person_channel(db, person, person_channel_type, normalized)
            _enrich_person(db, person, email=email, phone=phone, display_name=display_name)
            return ResolvedIdentity(person=person, channel=ch, created=False, channel_backfilled=backfilled)

    # --- Step 6: create new Person + PersonChannel ---
    person_email: str
    if person_channel_type == ChannelType.email and not is_placeholder_email(normalized):
        person_email = normalized
    elif email and not is_placeholder_email(email):
        person_email = _normalize_email_address(email) or email.strip()
    else:
        person_email = _email_from_address(person_channel_type.value, normalized)

    person_phone: str | None = None
    if person_channel_type in _PHONE_CHANNEL_TYPES:
        person_phone = normalized
    elif phone:
        person_phone = _normalize_phone_address(phone)

    first_name, last_name = "", ""
    if display_name:
        parts = display_name.split()
        first_name = parts[0]
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    person = Person(
        first_name=first_name or "Unknown",
        last_name=last_name or "Unknown",
        display_name=display_name,
        email=person_email,
        phone=person_phone,
        party_status=PartyStatus.lead,
    )
    db.add(person)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        # Race condition: another process created this person between our lookup
        # and insert. Re-run the lookup.
        return resolve_person(
            db,
            channel_type=channel_type,
            address=address,
            display_name=display_name,
            email=email,
            phone=phone,
        )

    channel = PersonChannel(
        person_id=person.id,
        channel_type=person_channel_type,
        address=normalized,
        is_primary=True,
    )
    db.add(channel)
    db.flush()

    logger.info(
        "person_identity_created person_id=%s channel_type=%s address=%s",
        person.id,
        person_channel_type.value,
        normalized,
    )
    return ResolvedIdentity(person=person, channel=channel, created=True, channel_backfilled=False)
