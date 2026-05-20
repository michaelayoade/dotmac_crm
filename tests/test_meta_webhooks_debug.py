from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel
from app.services.meta_webhooks_debug import get_recent_meta_message_attribution, inspect_messenger_sender_diagnostics


def _person(db_session, *, name: str, display_name: str | None = None, metadata: dict | None = None) -> Person:
    person = Person(
        first_name=name,
        last_name="Contact",
        display_name=display_name,
        email=f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        metadata_=metadata,
    )
    db_session.add(person)
    db_session.flush()
    return person


def _message(
    db_session,
    *,
    person: Person,
    channel_type: ChannelType,
    metadata: dict | None = None,
    conversation_metadata: dict | None = None,
) -> Message:
    conversation = Conversation(
        person_id=person.id,
        status=ConversationStatus.open,
        metadata_=conversation_metadata,
    )
    db_session.add(conversation)
    db_session.flush()
    message = Message(
        conversation_id=conversation.id,
        channel_type=channel_type,
        direction=MessageDirection.inbound,
        body="hello",
        received_at=datetime.now(UTC),
        metadata_=metadata,
    )
    db_session.add(message)
    db_session.commit()
    return message


def test_recent_meta_message_report_detects_ads_and_placeholders(db_session):
    ads_person = _person(
        db_session,
        name="Ads",
        display_name="Real IG Name",
        metadata={
            "instagram_profile": {
                "platform": "instagram",
                "sender_id": "ig-1",
                "sender_username": "real_ig",
                "sender_name": "Real IG Name",
            }
        },
    )
    _message(
        db_session,
        person=ads_person,
        channel_type=ChannelType.instagram_dm,
        metadata={
            "sender_id": "ig-1",
            "sender_username": "real_ig",
            "attribution": {"source": "ADS", "ad_id": "ad-1"},
        },
        conversation_metadata={"attribution": {"source": "ADS", "ad_id": "ad-1"}},
    )

    fallback_person = _person(
        db_session,
        name="Fallback",
        display_name="Facebook User 12345",
    )
    _message(
        db_session,
        person=fallback_person,
        channel_type=ChannelType.facebook_messenger,
        metadata={"sender_id": "12345"},
    )

    report = get_recent_meta_message_attribution(db_session, limit=10)

    assert report["summary"]["total_checked"] == 2
    assert report["summary"]["ads_detected"] == 1
    assert report["summary"]["facebook_fallback_placeholders"] == 1
    classes = {item["classification"] for item in report["items"]}
    assert "meta_ads_attributed" in classes
    assert "missing_identity_fallback" in classes


def test_recent_meta_message_report_detects_attribution_loss(db_session):
    person = _person(
        db_session,
        name="Lost",
        display_name="Real FB Name",
        metadata={
            "facebook_profile": {
                "platform": "facebook",
                "sender_id": "fb-1",
                "sender_name": "Real FB Name",
            }
        },
    )
    _message(
        db_session,
        person=person,
        channel_type=ChannelType.facebook_messenger,
        metadata={
            "sender_id": "fb-1",
            "raw": {
                "referral": {
                    "source": "ADS",
                    "ad_id": "fb-ad-1",
                }
            },
        },
    )

    report = get_recent_meta_message_attribution(db_session, limit=5)

    assert report["summary"]["attribution_missing"] == 1
    assert report["items"][0]["classification"] == "attribution_lost_between_webhook_and_persistence"


def test_recent_meta_message_report_includes_whatsapp_when_present(db_session):
    person = _person(
        db_session,
        name="WhatsApp",
        display_name="Nancy",
    )
    _message(
        db_session,
        person=person,
        channel_type=ChannelType.whatsapp,
        metadata={
            "raw": {
                "contacts": [{"profile": {"name": "Nancy"}, "wa_id": "2348000000000"}],
                "messages": [{"from": "2348000000000"}],
            }
        },
    )

    report = get_recent_meta_message_attribution(db_session, limit=5)

    assert report["summary"]["total_checked"] == 1
    assert report["items"][0]["provider"] == "whatsapp"
    assert report["items"][0]["classification"] == "whatsapp_organic"


def test_recent_meta_message_report_provider_filter_excludes_whatsapp(db_session):
    whatsapp_person = _person(db_session, name="WhatsAppOnly", display_name="WA Contact")
    _message(
        db_session,
        person=whatsapp_person,
        channel_type=ChannelType.whatsapp,
        metadata={
            "raw": {
                "contacts": [{"profile": {"name": "WA Contact"}, "wa_id": "2348000000001"}],
                "messages": [{"from": "2348000000001"}],
            }
        },
    )

    instagram_person = _person(
        db_session,
        name="InstagramOnly",
        display_name="Instagram User ig-123",
    )
    _message(
        db_session,
        person=instagram_person,
        channel_type=ChannelType.instagram_dm,
        metadata={"sender_id": "ig-123"},
    )

    report = get_recent_meta_message_attribution(db_session, limit=10, providers=("instagram", "facebook"))

    assert report["providers"] == ["instagram", "facebook"]
    assert report["summary"]["total_checked"] == 1
    assert report["items"][0]["provider"] == "instagram"


def test_inspect_messenger_sender_diagnostics_reports_pending_attribution(db_session):
    person = _person(
        db_session,
        name="Messenger",
        display_name="Facebook User 123456",
    )
    channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.facebook_messenger,
        address="123456",
        is_primary=True,
        metadata_={
            "pending_meta_attribution": {
                "page_id": "page-1",
                "attribution": {"source": "ADS", "ad_id": "ad-1"},
                "captured_at": datetime.now(UTC).isoformat(),
            }
        },
    )
    db_session.add(channel)
    db_session.flush()
    _message(
        db_session,
        person=person,
        channel_type=ChannelType.facebook_messenger,
        metadata={"sender_id": "123456"},
    )

    report = inspect_messenger_sender_diagnostics(db_session, sender_id="123456", page_id="page-1", limit=5)

    assert report["person"]["placeholder_name"] is True
    assert report["channel"]["pending_meta_attribution"]["attribution"]["ad_id"] == "ad-1"
    assert report["suspected_ad_without_persistence"] is True
