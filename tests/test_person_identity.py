"""Tests for unified person identity resolution."""

import uuid

from app.models.person import ChannelType, PartyStatus, Person, PersonChannel
from app.services.person_identity import (
    ensure_person_channel,
    is_placeholder_email,
    resolve_person,
)


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:12]}@example.com"


def _make_person(db, *, email=None, phone=None, display_name=None, first_name="Test", last_name="User"):
    person = Person(
        first_name=first_name,
        last_name=last_name,
        email=email or _unique_email(),
        phone=phone,
        display_name=display_name,
    )
    db.add(person)
    db.flush()
    return person


def _make_channel(db, person, channel_type, address, is_primary=True):
    ch = PersonChannel(
        person_id=person.id,
        channel_type=channel_type,
        address=address,
        is_primary=is_primary,
    )
    db.add(ch)
    db.flush()
    return ch


# ---------------------------------------------------------------------------
# is_placeholder_email
# ---------------------------------------------------------------------------


class TestIsPlaceholderEmail:
    def test_example_invalid(self):
        assert is_placeholder_email("whatsapp-123@example.invalid") is True

    def test_widget_local(self):
        assert is_placeholder_email("sess-abc@widget.local") is True

    def test_placeholder_local(self):
        assert is_placeholder_email("chatwoot-1@placeholder.local") is True

    def test_reseller_placeholder_domain(self):
        assert is_placeholder_email("org-123@reseller.dotmac.ng") is True

    def test_real_email(self):
        assert is_placeholder_email("alice@company.com") is False

    def test_none(self):
        assert is_placeholder_email(None) is False

    def test_empty(self):
        assert is_placeholder_email("") is False


# ---------------------------------------------------------------------------
# ensure_person_channel
# ---------------------------------------------------------------------------


class TestEnsurePersonChannel:
    def test_creates_new_channel(self, db_session):
        person = _make_person(db_session)
        ch, created = ensure_person_channel(db_session, person, ChannelType.whatsapp, "+2348012345678")
        assert created is True
        assert ch.person_id == person.id
        assert ch.channel_type == ChannelType.whatsapp
        assert ch.address == "+2348012345678"
        assert ch.is_primary is True

    def test_returns_existing_channel(self, db_session):
        person = _make_person(db_session)
        _make_channel(db_session, person, ChannelType.email, "alice@example.com")
        ch, created = ensure_person_channel(db_session, person, ChannelType.email, "alice@example.com")
        assert created is False
        assert ch.address == "alice@example.com"

    def test_second_channel_not_primary(self, db_session):
        person = _make_person(db_session)
        _make_channel(db_session, person, ChannelType.whatsapp, "+1111", is_primary=True)
        ch, created = ensure_person_channel(db_session, person, ChannelType.whatsapp, "+2222")
        assert created is True
        assert ch.is_primary is False


# ---------------------------------------------------------------------------
# resolve_person — exact channel match
# ---------------------------------------------------------------------------


class TestResolvePersonExactChannel:
    def test_finds_existing_person_by_channel(self, db_session):
        person = _make_person(db_session, phone="+2348012345678")
        _make_channel(db_session, person, ChannelType.whatsapp, "+2348012345678")
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2348012345678",
        )
        assert result.person.id == person.id
        assert result.created is False
        assert result.channel.channel_type == ChannelType.whatsapp

    def test_finds_existing_person_by_email_channel(self, db_session):
        email = _unique_email()
        person = _make_person(db_session, email=email)
        _make_channel(db_session, person, ChannelType.email, email)
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.email,
            address=email,
        )
        assert result.person.id == person.id
        assert result.created is False


# ---------------------------------------------------------------------------
# resolve_person — cross-type channel match
# ---------------------------------------------------------------------------


class TestResolvePersonCrossType:
    def test_whatsapp_finds_phone_channel(self, db_session):
        person = _make_person(db_session, phone="+2348012345678")
        _make_channel(db_session, person, ChannelType.phone, "+2348012345678")
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2348012345678",
        )
        assert result.person.id == person.id
        assert result.created is False
        assert result.channel_backfilled is True
        assert result.channel.channel_type == ChannelType.whatsapp

    def test_sms_finds_whatsapp_channel(self, db_session):
        person = _make_person(db_session, phone="+2348099999999")
        _make_channel(db_session, person, ChannelType.whatsapp, "+2348099999999")
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.sms,
            address="+2348099999999",
        )
        assert result.person.id == person.id
        assert result.channel.channel_type == ChannelType.sms
        assert result.channel_backfilled is True


# ---------------------------------------------------------------------------
# resolve_person — Person.email fallback
# ---------------------------------------------------------------------------


class TestResolvePersonEmailFallback:
    def test_finds_by_person_email_no_channel(self, db_session):
        """Person exists with email but NO PersonChannel rows (ERP import case)."""
        email = _unique_email()
        person = _make_person(db_session, email=email)
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.email,
            address=email,
        )
        assert result.person.id == person.id
        assert result.created is False
        assert result.channel_backfilled is True
        assert result.channel.channel_type == ChannelType.email

    def test_case_insensitive_email_match(self, db_session):
        email = _unique_email()
        person = _make_person(db_session, email=email)
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.email,
            address=email.upper(),
        )
        assert result.person.id == person.id


# ---------------------------------------------------------------------------
# resolve_person — Person.phone fallback
# ---------------------------------------------------------------------------


class TestResolvePersonPhoneFallback:
    def test_finds_by_person_phone_no_channel(self, db_session):
        """Person exists with phone but NO PersonChannel rows (ERP import case)."""
        person = _make_person(db_session, phone="+2348012345678")
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2348012345678",
        )
        assert result.person.id == person.id
        assert result.created is False
        assert result.channel_backfilled is True

    def test_phone_with_raw_format(self, db_session):
        """Person.phone stored with + prefix, searched with raw digits."""
        person = _make_person(db_session, phone="+2348012345678")
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="2348012345678",
        )
        assert result.person.id == person.id


# ---------------------------------------------------------------------------
# resolve_person — cross-identifier hints
# ---------------------------------------------------------------------------


class TestResolvePersonCrossHints:
    def test_whatsapp_with_email_hint(self, db_session):
        """WhatsApp message arrives with email metadata → matches Person by email."""
        email = _unique_email()
        person = _make_person(db_session, email=email)
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2349087654321",
            email=email,
        )
        assert result.person.id == person.id
        assert result.channel.channel_type == ChannelType.whatsapp
        assert result.channel_backfilled is True

    def test_email_with_phone_hint(self, db_session):
        """Email arrives with phone hint → matches Person by phone."""
        person = _make_person(db_session, phone="+2348012345678")
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.email,
            address=_unique_email(),
            phone="+2348012345678",
        )
        assert result.person.id == person.id
        assert result.channel.channel_type == ChannelType.email


# ---------------------------------------------------------------------------
# resolve_person — new person creation
# ---------------------------------------------------------------------------


class TestResolvePersonCreation:
    def test_creates_new_person(self, db_session):
        result = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2349011111111",
            display_name="New Person",
        )
        assert result.created is True
        assert result.person.display_name == "New Person"
        assert result.person.first_name == "New"
        assert result.person.last_name == "Person"
        assert result.person.phone == "+2349011111111"
        assert result.person.party_status == PartyStatus.lead
        assert result.channel.channel_type == ChannelType.whatsapp
        assert result.channel.is_primary is True

    def test_creates_new_person_email_channel(self, db_session):
        email = _unique_email()
        result = resolve_person(
            db_session,
            channel_type=ChannelType.email,
            address=email,
        )
        assert result.created is True
        assert result.person.email == email

    def test_placeholder_email_for_non_email_channel(self, db_session):
        result = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2349022222222",
        )
        assert result.created is True
        assert is_placeholder_email(result.person.email) is True


# ---------------------------------------------------------------------------
# resolve_person — placeholder email replacement
# ---------------------------------------------------------------------------


class TestResolvePersonPlaceholderReplacement:
    def test_replaces_placeholder_email(self, db_session):
        person = _make_person(db_session, email="whatsapp-123@example.invalid", phone="+2348012345678")
        _make_channel(db_session, person, ChannelType.whatsapp, "+2348012345678")
        db_session.commit()

        real_email = _unique_email()
        result = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2348012345678",
            email=real_email,
        )
        assert result.person.email == real_email

    def test_does_not_replace_real_email(self, db_session):
        original = _unique_email()
        person = _make_person(db_session, email=original, phone="+2348012345678")
        _make_channel(db_session, person, ChannelType.whatsapp, "+2348012345678")
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2348012345678",
            email="other@example.com",
        )
        assert result.person.email == original


# ---------------------------------------------------------------------------
# resolve_person — phone enrichment
# ---------------------------------------------------------------------------


class TestResolvePersonPhoneEnrichment:
    def test_enriches_phone_on_existing_person(self, db_session):
        email = _unique_email()
        person = _make_person(db_session, email=email, phone=None)
        _make_channel(db_session, person, ChannelType.email, email)
        db_session.commit()

        result = resolve_person(
            db_session,
            channel_type=ChannelType.email,
            address=email,
            phone="+2348055555555",
        )
        assert result.person.phone == "+2348055555555"


# ---------------------------------------------------------------------------
# resolve_person — idempotency
# ---------------------------------------------------------------------------


class TestResolvePersonIdempotency:
    def test_second_call_does_not_duplicate(self, db_session):
        result1 = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2349033333333",
            display_name="Idem Person",
        )
        db_session.commit()

        result2 = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2349033333333",
        )
        assert result2.person.id == result1.person.id
        assert result2.created is False
        assert result2.channel.id == result1.channel.id

    def test_display_name_enrichment_on_second_call(self, db_session):
        result1 = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2349044444444",
        )
        db_session.commit()
        assert result1.person.display_name is None

        result2 = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2349044444444",
            display_name="Late Name",
        )
        assert result2.person.display_name == "Late Name"


# ---------------------------------------------------------------------------
# resolve_person — placeholder email ignored in lookup
# ---------------------------------------------------------------------------


class TestResolvePersonPlaceholderIgnored:
    def test_placeholder_email_not_used_for_lookup(self, db_session):
        """Two different WhatsApp numbers that both generated placeholder emails
        should NOT be linked to the same person."""
        result1 = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2349055555555",
        )
        db_session.commit()

        result2 = resolve_person(
            db_session,
            channel_type=ChannelType.whatsapp,
            address="+2349066666666",
        )
        assert result2.person.id != result1.person.id
