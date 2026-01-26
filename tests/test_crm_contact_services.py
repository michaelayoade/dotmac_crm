"""Tests for CRM contact service."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.crm.enums import ChannelType
from app.schemas.crm.contact import ContactCreate, ContactUpdate, ContactChannelCreate, ContactChannelUpdate
from app.services.crm import contact as contact_service


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


# =============================================================================
# Contacts CRUD Tests
# =============================================================================


def test_create_contact(db_session):
    """Test creating a CRM contact."""
    email = _unique_email()
    contact = contact_service.Contacts.create(
        db_session,
        ContactCreate(
            first_name="John",
            last_name="Doe",
            display_name="John Doe",
            email=email,
            phone="+15551234567",
        ),
    )
    assert contact.display_name == "John Doe"
    assert contact.email == email
    assert contact.phone == "15551234567"
    assert contact.is_active is True


def test_create_contact_minimal(db_session):
    """Test creating a contact with minimal fields."""
    contact = contact_service.Contacts.create(
        db_session,
        ContactCreate(
            first_name="Minimal",
            last_name="Contact",
            email=_unique_email(),
        ),
    )
    assert contact.first_name == "Minimal"
    assert contact.last_name == "Contact"
    assert contact.email
    assert contact.phone is None


def test_get_contact(db_session, crm_contact):
    """Test getting a contact by ID."""
    fetched = contact_service.Contacts.get(db_session, str(crm_contact.id))
    assert fetched.id == crm_contact.id
    assert fetched.display_name == crm_contact.display_name


def test_get_contact_not_found(db_session):
    """Test getting non-existent contact raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        contact_service.Contacts.get(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Contact not found" in exc_info.value.detail


def test_list_contacts(db_session):
    """Test listing contacts."""
    # Create test contacts
    for i in range(3):
        contact_service.Contacts.create(
            db_session,
            ContactCreate(
                first_name="Contact",
                last_name=str(i),
                display_name=f"Contact {i}",
                email=_unique_email(),
            ),
        )

    contacts = contact_service.Contacts.list(
        db_session,
        person_id=None,
        organization_id=None,
        is_active=None,
        search=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(contacts) >= 3


def test_list_contacts_with_search(db_session):
    """Test listing contacts with search filter."""
    unique_name = f"SearchTest-{uuid.uuid4().hex[:8]}"
    contact_service.Contacts.create(
        db_session,
        ContactCreate(
            first_name="Search",
            last_name="Test",
            display_name=unique_name,
            email=_unique_email(),
        ),
    )

    contacts = contact_service.Contacts.list(
        db_session,
        person_id=None,
        organization_id=None,
        is_active=None,
        search=unique_name,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(contacts) == 1
    assert contacts[0].display_name == unique_name


def test_list_contacts_filter_by_person(db_session, person):
    """Test listing contacts filtered by person_id."""
    contact = contact_service.Contacts.create(
        db_session,
        ContactCreate(
            first_name="Person",
            last_name="Contact",
            display_name="Person Contact",
            email=_unique_email(),
        ),
    )

    contacts = contact_service.Contacts.list(
        db_session,
        person_id=str(contact.id),
        organization_id=None,
        is_active=None,
        search=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(contacts) >= 1
    assert all(c.id == contact.id for c in contacts)


def test_list_contacts_filter_inactive(db_session):
    """Test listing only inactive contacts."""
    contact = contact_service.Contacts.create(
        db_session,
        ContactCreate(
            first_name="Inactive",
            last_name="Contact",
            display_name="Inactive Contact",
            email=_unique_email(),
            is_active=False,
        ),
    )

    contacts = contact_service.Contacts.list(
        db_session,
        person_id=None,
        organization_id=None,
        is_active=False,
        search=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(c.id == contact.id for c in contacts)


def test_list_contacts_invalid_order_by(db_session):
    """Test listing contacts with invalid order_by raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        contact_service.Contacts.list(
            db_session,
            person_id=None,
            organization_id=None,
            is_active=None,
            search=None,
            order_by="invalid_column",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400
    assert "Invalid order_by" in exc_info.value.detail


def test_update_contact(db_session, crm_contact):
    """Test updating a contact."""
    updated = contact_service.Contacts.update(
        db_session,
        str(crm_contact.id),
        ContactUpdate(display_name="Updated Name"),
    )
    assert updated.display_name == "Updated Name"
    assert updated.id == crm_contact.id


def test_update_contact_not_found(db_session):
    """Test updating non-existent contact raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        contact_service.Contacts.update(
            db_session,
            str(uuid.uuid4()),
            ContactUpdate(display_name="New Name"),
        )
    assert exc_info.value.status_code == 404


def test_delete_contact(db_session):
    """Test deleting (soft delete) a contact."""
    contact = contact_service.Contacts.create(
        db_session,
        ContactCreate(
            first_name="To",
            last_name="Delete",
            display_name="To Delete",
            email=_unique_email(),
        ),
    )
    contact_service.Contacts.delete(db_session, str(contact.id))
    db_session.refresh(contact)
    assert contact.is_active is False


def test_delete_contact_not_found(db_session):
    """Test deleting non-existent contact raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        contact_service.Contacts.delete(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


# =============================================================================
# Contact Channels Tests
# =============================================================================


def test_create_contact_channel(db_session, crm_contact):
    """Test creating a contact channel."""
    channel = contact_service.ContactChannels.create(
        db_session,
        ContactChannelCreate(
            person_id=crm_contact.id,
            channel_type=ChannelType.email,
            address="channel@example.com",
            is_primary=True,
        ),
    )
    assert channel.person_id == crm_contact.id
    assert channel.channel_type == ChannelType.email
    assert channel.address == "channel@example.com"
    assert channel.is_primary is True


def test_create_contact_channel_contact_not_found(db_session):
    """Test creating channel for non-existent contact raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        contact_service.ContactChannels.create(
            db_session,
            ContactChannelCreate(
                person_id=uuid.uuid4(),
                channel_type=ChannelType.email,
                address="test@example.com",
            ),
        )
    assert exc_info.value.status_code == 404
    assert "Contact not found" in exc_info.value.detail


def test_create_channel_sets_primary_exclusive(db_session, crm_contact):
    """Test that setting a channel as primary unsets other primary channels."""
    # Create first primary channel
    channel1 = contact_service.ContactChannels.create(
        db_session,
        ContactChannelCreate(
            person_id=crm_contact.id,
            channel_type=ChannelType.email,
            address="first@example.com",
            is_primary=True,
        ),
    )
    assert channel1.is_primary is True

    # Create second primary channel of same type
    channel2 = contact_service.ContactChannels.create(
        db_session,
        ContactChannelCreate(
            person_id=crm_contact.id,
            channel_type=ChannelType.email,
            address="second@example.com",
            is_primary=True,
        ),
    )

    # Refresh first channel and verify it's no longer primary
    db_session.refresh(channel1)
    assert channel1.is_primary is False
    assert channel2.is_primary is True


def test_list_contact_channels(db_session, crm_contact):
    """Test listing contact channels."""
    contact_service.ContactChannels.create(
        db_session,
        ContactChannelCreate(
            person_id=crm_contact.id,
            channel_type=ChannelType.email,
            address="list@example.com",
        ),
    )

    channels = contact_service.ContactChannels.list(
        db_session,
        person_id=str(crm_contact.id),
        channel_type=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(channels) >= 1


def test_list_contact_channels_filter_by_type(db_session, crm_contact):
    """Test listing channels filtered by channel type."""
    contact_service.ContactChannels.create(
        db_session,
        ContactChannelCreate(
            person_id=crm_contact.id,
            channel_type=ChannelType.whatsapp,
            address="+15559876543",
        ),
    )

    channels = contact_service.ContactChannels.list(
        db_session,
        person_id=str(crm_contact.id),
        channel_type="whatsapp",
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(c.channel_type == ChannelType.whatsapp for c in channels)


def test_list_contact_channels_invalid_type(db_session):
    """Test listing channels with invalid channel type raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        contact_service.ContactChannels.list(
            db_session,
            person_id=None,
            channel_type="invalid_type",
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400
    assert "Invalid channel_type" in exc_info.value.detail


def test_update_contact_channel(db_session, crm_contact):
    """Test updating a contact channel."""
    channel = contact_service.ContactChannels.create(
        db_session,
        ContactChannelCreate(
            person_id=crm_contact.id,
            channel_type=ChannelType.email,
            address="update@example.com",
        ),
    )

    updated = contact_service.ContactChannels.update(
        db_session,
        str(channel.id),
        ContactChannelUpdate(address="updated@example.com"),
    )
    assert updated.address == "updated@example.com"


def test_update_contact_channel_not_found(db_session):
    """Test updating non-existent channel raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        contact_service.ContactChannels.update(
            db_session,
            str(uuid.uuid4()),
            ContactChannelUpdate(address="new@example.com"),
        )
    assert exc_info.value.status_code == 404


def test_update_channel_sets_primary_exclusive(db_session, crm_contact):
    """Test that updating channel to primary unsets other primary channels."""
    channel1 = contact_service.ContactChannels.create(
        db_session,
        ContactChannelCreate(
            person_id=crm_contact.id,
            channel_type=ChannelType.email,
            address="ch1@example.com",
            is_primary=True,
        ),
    )
    channel2 = contact_service.ContactChannels.create(
        db_session,
        ContactChannelCreate(
            person_id=crm_contact.id,
            channel_type=ChannelType.email,
            address="ch2@example.com",
            is_primary=False,
        ),
    )

    # Update channel2 to be primary
    contact_service.ContactChannels.update(
        db_session,
        str(channel2.id),
        ContactChannelUpdate(is_primary=True),
    )

    db_session.refresh(channel1)
    db_session.refresh(channel2)
    assert channel1.is_primary is False
    assert channel2.is_primary is True


# =============================================================================
# get_or_create_contact_by_channel Tests
# =============================================================================


def test_get_or_create_contact_creates_new(db_session):
    """Test get_or_create creates new contact when not found."""
    unique_email = _unique_email()
    contact, channel = contact_service.get_or_create_contact_by_channel(
        db_session,
        channel_type=ChannelType.email,
        address=unique_email,
        display_name="New Contact",
    )
    assert contact.display_name == "New Contact"
    assert contact.email == unique_email
    assert channel.channel_type == ChannelType.email
    assert channel.address == unique_email
    assert channel.is_primary is True


def test_get_or_create_contact_returns_existing(db_session, crm_contact, crm_contact_channel):
    """Test get_or_create returns existing contact when channel exists."""
    contact, channel = contact_service.get_or_create_contact_by_channel(
        db_session,
        channel_type=crm_contact_channel.channel_type,
        address=crm_contact_channel.address,
        display_name="Ignored Name",
    )
    assert contact.id == crm_contact.id
    assert channel.id == crm_contact_channel.id


def test_get_or_create_contact_whatsapp(db_session):
    """Test get_or_create with WhatsApp channel."""
    phone = f"+1555{uuid.uuid4().hex[:7]}"
    contact, channel = contact_service.get_or_create_contact_by_channel(
        db_session,
        channel_type=ChannelType.whatsapp,
        address=phone,
        display_name="WhatsApp User",
    )
    assert contact.phone == phone.replace("+", "")
    assert contact.email
    assert channel.channel_type == ChannelType.whatsapp
