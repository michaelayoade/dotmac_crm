"""
Contact/person resolution for inbound CRM messages.

This module handles resolving or creating Person records for inbound messages
across different channels (email, WhatsApp, SMS, etc.). It ensures that
contacts have appropriate channel records and normalizes addresses.
"""

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.crm.enums import ChannelType
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel
from app.services.crm import contact as contact_service
from app.services.crm.inbox_normalizers import _normalize_channel_address


def _resolve_person_for_contact(contact: Person) -> str:
    """
    Get the person ID from a contact.

    Args:
        contact: The Person object to resolve.

    Returns:
        The string representation of the person's UUID.
    """
    return str(contact.id)


def _ensure_person_channel(
    db: Session,
    person: Person,
    channel_type: ChannelType,
    address: str,
) -> PersonChannel:
    """
    Ensure a person has a channel record for the given address.

    If the channel already exists, returns the existing record. Otherwise,
    creates a new channel record. The first channel of a given type for a
    person is marked as primary.

    Args:
        db: Database session.
        person: The Person to ensure has the channel.
        channel_type: The type of channel (email, whatsapp, sms, etc.).
        address: The channel address (email, phone number, etc.).

    Returns:
        The existing or newly created PersonChannel record.
    """
    person_channel_type = PersonChannelType(channel_type.value)
    normalized_address = _normalize_channel_address(channel_type, address) or address
    channel = (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == person.id)
        .filter(PersonChannel.channel_type == person_channel_type)
        .filter(
            or_(
                PersonChannel.address == normalized_address,
                PersonChannel.address == address.strip(),
            )
        )
        .first()
    )
    if channel:
        return channel
    has_primary = (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == person.id)
        .filter(PersonChannel.channel_type == person_channel_type)
        .filter(PersonChannel.is_primary.is_(True))
        .first()
    )
    channel = PersonChannel(
        person_id=person.id,
        channel_type=person_channel_type,
        address=normalized_address,
        is_primary=has_primary is None,
    )
    db.add(channel)
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
    """
    Resolve or create a person for an inbound message.

    This function attempts to find an existing person by:
    1. Looking up existing PersonChannel records matching the address.
    2. For email channels, checking the Person.email field.
    3. For WhatsApp channels, checking the Person.phone field.

    If no existing person is found, creates a new contact via the contact
    service.

    Args:
        db: Database session.
        channel_type: The type of channel the message arrived on.
        address: The sender's address (email, phone number, etc.).
        display_name: Optional display name from the message.
        account: Optional account to associate with a new contact.

    Returns:
        A tuple of (Person, PersonChannel) for the resolved or created contact.
    """
    person_channel_type = PersonChannelType(channel_type.value)
    normalized_address = _normalize_channel_address(channel_type, address)
    existing_channel = (
        db.query(PersonChannel)
        .filter(PersonChannel.channel_type == person_channel_type)
        .filter(
            or_(
                PersonChannel.address == normalized_address,
                PersonChannel.address == address.strip(),
            )
        )
        .first()
    )
    if existing_channel:
        return existing_channel.person, existing_channel

    person = None
    if channel_type == ChannelType.email:
        person = db.query(Person).filter(func.lower(Person.email) == normalized_address).first()
    if not person and channel_type == ChannelType.whatsapp:
        person = (
            db.query(Person)
            .filter(
                or_(
                    Person.phone == normalized_address,
                    Person.phone == address.strip(),
                )
            )
            .first()
        )

    if person:
        channel = _ensure_person_channel(
            db,
            person,
            channel_type,
            normalized_address or address,
        )
        needs_commit = False
        if (
            channel_type == ChannelType.email
            and normalized_address
            and (not person.email or person.email.endswith("@example.invalid"))
        ):
            person.email = normalized_address
            needs_commit = True
        if display_name and not person.display_name:
            person.display_name = display_name
            needs_commit = True
        if needs_commit:
            db.commit()
            db.refresh(person)
        return person, channel

    return contact_service.get_or_create_contact_by_channel(
        db,
        channel_type,
        normalized_address or address,
        display_name,
    )
