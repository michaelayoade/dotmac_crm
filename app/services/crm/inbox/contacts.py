"""
Contact/person resolution for inbound CRM messages.

This module delegates to the unified ``person_identity`` module for all
person lookup-or-create operations. Legacy callers continue to use the
same function signatures.
"""

from sqlalchemy.orm import Session

from app.models.crm.enums import ChannelType
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel


def _resolve_person_for_contact(contact: Person) -> str:
    """Get the person ID from a contact."""
    return str(contact.id)


def _ensure_person_channel(
    db: Session,
    person: Person,
    channel_type: ChannelType,
    address: str,
) -> PersonChannel:
    """Ensure a person has a channel record for the given address.

    Delegates to ``person_identity.ensure_person_channel``.
    """
    from app.services.person_identity import ensure_person_channel as unified_ensure

    person_channel_type = PersonChannelType(channel_type.value)
    channel, _created = unified_ensure(db, person, person_channel_type, address)
    db.commit()
    db.refresh(channel)
    return channel


def _resolve_person_for_inbound(
    db: Session,
    channel_type: ChannelType,
    address: str,
    display_name: str | None,
    account=None,
) -> tuple[Person, PersonChannel]:
    """Resolve or create a person for an inbound message.

    Delegates to ``person_identity.resolve_person``.
    """
    from app.services.person_identity import resolve_person

    result = resolve_person(
        db,
        channel_type=channel_type,
        address=address,
        display_name=display_name,
    )
    db.commit()
    db.refresh(result.person)
    db.refresh(result.channel)
    return result.person, result.channel
