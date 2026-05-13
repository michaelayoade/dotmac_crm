from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.sales import Lead
from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.meta_raw_event import MetaRawEvent
from app.models.oauth_token import OAuthToken
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel
from app.schemas.crm.inbox import FacebookMessengerWebhookPayload, InstagramDMWebhookPayload, MetaWebhookPayload
from app.services import meta_webhooks


def _seed_meta_page_token(db_session, *, page_id: str = "page_123") -> ConnectorConfig:
    config = ConnectorConfig(name=f"Meta Connector {page_id}", connector_type=ConnectorType.facebook, is_active=True)
    db_session.add(config)
    db_session.flush()
    db_session.add(
        IntegrationTarget(
            name="CRM",
            target_type=IntegrationTargetType.crm,
            connector_config_id=config.id,
            is_active=True,
        )
    )
    db_session.add(
        OAuthToken(
            connector_config_id=config.id,
            provider="meta",
            account_type="page",
            external_account_id=page_id,
            external_account_name="Test Page",
            access_token="page-token",
            token_expires_at=datetime.now(UTC) + timedelta(days=30),
            scopes=["pages_show_list", "leads_retrieval"],
            is_active=True,
        )
    )
    db_session.commit()
    return config


def test_normalize_external_id_and_ref():
    short_digest, short_raw = meta_webhooks._normalize_external_id("abc")
    assert short_digest == "abc"
    assert short_raw is None
    long_value = "x" * 130
    digest, raw = meta_webhooks._normalize_external_id(long_value)
    assert raw == long_value
    assert digest is not None
    assert len(digest) == 64
    assert meta_webhooks._normalize_external_ref("x" * 300) is None


def test_normalize_phone_address():
    assert meta_webhooks._normalize_phone_address(" (555) 123-4567 ") == "+5551234567"


def test_coerce_identity_dict_and_extract():
    identity = meta_webhooks._extract_identity_metadata(
        {"email": "test@example.com"},
        '{"phone": "+1555123456"}',
    )
    assert identity["email"] == "test@example.com"
    assert identity["phone"] == "+1555123456"


def test_extract_meta_attribution_from_nested_referral_payload():
    attribution = meta_webhooks._extract_meta_attribution(
        {
            "referral": {
                "source": "ADS",
                "ad_id": "12001",
                "campaign_id": "8899",
                "utm_source": "meta",
                "utm_campaign": "fiber-promo",
            }
        }
    )
    assert attribution["source"] == "ADS"
    assert attribution["ad_id"] == "12001"
    assert attribution["campaign_id"] == "8899"
    assert attribution["utm_source"] == "meta"
    assert attribution["utm_campaign"] == "fiber-promo"


def test_upsert_entity_attribution_metadata_updates_last_seen_and_channel():
    class DummyEntity:
        def __init__(self):
            self.metadata_ = {"attribution": {"ad_id": "old"}}

    entity = DummyEntity()
    meta_webhooks._upsert_entity_attribution_metadata(
        entity,
        attribution={"campaign_id": "new-campaign"},
        channel=meta_webhooks.ChannelType.facebook_messenger,
    )
    attribution = entity.metadata_["attribution"]
    assert attribution["ad_id"] == "old"
    assert attribution["campaign_id"] == "new-campaign"
    assert attribution["last_channel"] == "facebook_messenger"
    assert isinstance(attribution["last_seen_at"], str)


def test_persist_meta_attribution_to_person_and_lead_respects_setting_gate(db_session):
    person = Person(first_name="Meta", last_name="Lead", email="meta-lead@example.com")
    db_session.add(person)
    db_session.add(
        DomainSetting(
            domain=SettingDomain.comms,
            key="meta_capture_ad_attribution",
            value_type=SettingValueType.boolean,
            value_text="false",
            is_active=True,
        )
    )
    db_session.commit()

    meta_webhooks._persist_meta_attribution_to_person_and_lead(
        db_session,
        person=person,
        channel=ChannelType.whatsapp,
        attribution={"source": "ADS", "ad_id": "ad-1"},
    )

    db_session.refresh(person)
    assert not person.metadata_


def test_extract_location_from_attachments():
    attachments = [
        {"type": "image", "payload": {}},
        {
            "type": "location",
            "payload": {"coordinates": {"lat": 1.0, "long": 2.0}, "title": "HQ"},
        },
    ]
    location = meta_webhooks._extract_location_from_attachments(attachments)
    assert location["latitude"] == 1.0
    assert location["longitude"] == 2.0
    assert location["label"] == "HQ"


def test_fetch_profile_name_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"name": "Test User"}

    with patch("app.services.meta_webhooks.httpx.Client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.get.return_value = mock_response
        mock_client.return_value.__enter__.return_value = mock_instance
        mock_client.return_value.__exit__.return_value = None

        result = meta_webhooks._fetch_profile_name(
            access_token="token",
            user_id="123",
            fields="name",
            base_url="https://graph.facebook.com/v19.0",
        )

    assert result == "Test User"


def test_process_instagram_webhook_preserves_sender_username_in_payload_metadata(db_session, monkeypatch):
    captured: dict[str, object] = {}

    def _receive(_db, payload):
        captured["payload"] = payload
        return SimpleNamespace(id="msg-1")

    monkeypatch.setattr(meta_webhooks, "_resolve_meta_connector", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(meta_webhooks, "receive_instagram_message", _receive)

    payload = MetaWebhookPayload(
        object="instagram",
        entry=[
            {
                "id": "17841403813819361",
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "981925791189944", "username": "real_handle"},
                        "recipient": {"id": "17841403813819361"},
                        "timestamp": 1710000000000,
                        "message": {"mid": "ig-mid-1", "text": "Hi"},
                    }
                ],
            }
        ],
    )

    results = meta_webhooks.process_instagram_webhook(db_session, payload)

    assert results == [{"message_id": "msg-1", "status": "received"}]
    parsed = captured["payload"]
    assert isinstance(parsed, InstagramDMWebhookPayload)
    assert parsed.contact_name == "real_handle"
    assert parsed.metadata["sender_id"] == "981925791189944"
    assert parsed.metadata["sender_username"] == "real_handle"


def test_process_instagram_webhook_uses_graph_lookup_name_when_sender_fields_missing(db_session, monkeypatch):
    captured: dict[str, object] = {}

    def _receive(_db, payload):
        captured["payload"] = payload
        return SimpleNamespace(id="msg-2")

    monkeypatch.setattr(meta_webhooks, "_resolve_meta_connector", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(meta_webhooks, "_fetch_profile_name", lambda *_args, **_kwargs: "Recovered Name")
    monkeypatch.setattr(meta_webhooks, "receive_instagram_message", _receive)

    payload = MetaWebhookPayload(
        object="instagram",
        entry=[
            {
                "id": "17841403813819361",
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "981925791189945"},
                        "recipient": {"id": "17841403813819361"},
                        "timestamp": 1710000000000,
                        "message": {"mid": "ig-mid-2", "text": "Hi"},
                    }
                ],
            }
        ],
    )

    results = meta_webhooks.process_instagram_webhook(db_session, payload)

    assert results == [{"message_id": "msg-2", "status": "received"}]
    parsed = captured["payload"]
    assert isinstance(parsed, InstagramDMWebhookPayload)
    assert parsed.contact_name == "Recovered Name"
    assert parsed.metadata["sender_id"] == "981925791189945"
    assert parsed.metadata["sender_name"] == "Recovered Name"
    assert parsed.metadata["platform"] == "instagram"


def test_process_instagram_webhook_ad_origin_preserves_identity_and_attribution(db_session, monkeypatch):
    captured: dict[str, object] = {}

    def _receive(_db, payload):
        captured["payload"] = payload
        return SimpleNamespace(id="msg-ig-ad-1")

    monkeypatch.setattr(meta_webhooks, "_resolve_meta_connector", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(meta_webhooks, "_fetch_profile_name", lambda *_args, **_kwargs: "Recovered IG Name")
    monkeypatch.setattr(meta_webhooks, "receive_instagram_message", _receive)

    payload = MetaWebhookPayload(
        object="instagram",
        entry=[
            {
                "id": "17841403813819361",
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "981925791189946"},
                        "recipient": {"id": "17841403813819361"},
                        "timestamp": 1710000000000,
                        "referral": {"source": "ADS", "ad_id": "ig_ad_1"},
                        "message": {"mid": "ig-mid-ad-1", "text": "Hi"},
                    }
                ],
            }
        ],
    )

    results = meta_webhooks.process_instagram_webhook(db_session, payload)

    assert results == [{"message_id": "msg-ig-ad-1", "status": "received"}]
    parsed = captured["payload"]
    assert isinstance(parsed, InstagramDMWebhookPayload)
    assert parsed.contact_name == "Recovered IG Name"
    assert parsed.metadata["sender_id"] == "981925791189946"
    assert parsed.metadata["sender_name"] == "Recovered IG Name"
    assert parsed.metadata["platform"] == "instagram"
    assert parsed.metadata["attribution"]["source"] == "ADS"
    assert parsed.metadata["attribution"]["ad_id"] == "ig_ad_1"


def test_process_messenger_webhook_preserves_sender_identity_in_payload_metadata(db_session, monkeypatch):
    captured: dict[str, object] = {}

    def _receive(_db, payload):
        captured["payload"] = payload
        return SimpleNamespace(id="fb-msg-1")

    monkeypatch.setattr(meta_webhooks, "_resolve_meta_connector", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(meta_webhooks, "receive_facebook_message", _receive)

    payload = MetaWebhookPayload(
        object="page",
        entry=[
            {
                "id": "page_123",
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "12345", "name": "Real Facebook Name"},
                        "recipient": {"id": "page_123"},
                        "timestamp": 1710000000000,
                        "message": {"mid": "fb-mid-1", "text": "Hi"},
                    }
                ],
            }
        ],
    )

    results = meta_webhooks.process_messenger_webhook(db_session, payload)

    assert results == [{"message_id": "fb-msg-1", "status": "received"}]
    parsed = captured["payload"]
    assert isinstance(parsed, FacebookMessengerWebhookPayload)
    assert parsed.contact_name == "Real Facebook Name"
    assert parsed.metadata["sender_id"] == "12345"
    assert parsed.metadata["sender_name"] == "Real Facebook Name"
    assert parsed.metadata["platform"] == "facebook"


def test_process_messenger_webhook_ad_origin_preserves_identity_and_attribution(db_session, monkeypatch):
    captured: dict[str, object] = {}

    def _receive(_db, payload):
        captured["payload"] = payload
        return SimpleNamespace(id="fb-msg-2")

    monkeypatch.setattr(meta_webhooks, "_resolve_meta_connector", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(meta_webhooks, "_fetch_profile_name", lambda *_args, **_kwargs: "Recovered FB Name")
    monkeypatch.setattr(meta_webhooks, "receive_facebook_message", _receive)

    payload = MetaWebhookPayload(
        object="page",
        entry=[
            {
                "id": "page_123",
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "12346"},
                        "recipient": {"id": "page_123"},
                        "timestamp": 1710000000000,
                        "referral": {"source": "ADS", "ad_id": "ad_123"},
                        "message": {"mid": "fb-mid-2", "text": "Hello"},
                    }
                ],
            }
        ],
    )

    results = meta_webhooks.process_messenger_webhook(db_session, payload)

    assert results == [{"message_id": "fb-msg-2", "status": "received"}]
    parsed = captured["payload"]
    assert isinstance(parsed, FacebookMessengerWebhookPayload)
    assert parsed.contact_name == "Recovered FB Name"
    assert parsed.metadata["sender_id"] == "12346"
    assert parsed.metadata["sender_name"] == "Recovered FB Name"
    assert parsed.metadata["platform"] == "facebook"
    assert parsed.metadata["attribution"]["source"] == "ADS"
    assert parsed.metadata["attribution"]["ad_id"] == "ad_123"


def test_persist_meta_raw_events_stores_full_message_event_and_attribution(db_session):
    payload = {
        "object": "page",
        "entry": [
            {
                "id": "page_123",
                "time": 1710000000,
                "messaging": [
                    {
                        "sender": {"id": "sender_123"},
                        "recipient": {"id": "page_123"},
                        "timestamp": 1710000000000,
                        "message": {"mid": "mid_123", "text": "Hello"},
                        "referral": {"source": "ADS", "ad_id": "ad_123", "campaign_id": "camp_123"},
                    }
                ],
            }
        ],
    }

    stored = meta_webhooks.persist_meta_raw_events(db_session, payload, trace_id="trace-123")

    assert len(stored) == 1
    event = db_session.query(MetaRawEvent).one()
    assert event.platform == "facebook_messenger"
    assert event.sender_id == "sender_123"
    assert event.page_id == "page_123"
    assert event.event_type == "message"
    assert event.external_message_id == "mid_123"
    assert event.trace_id == "trace-123"
    assert event.raw_payload["referral"]["ad_id"] == "ad_123"
    assert event.attribution["campaign_id"] == "camp_123"


def test_persist_instagram_sender_identity_updates_placeholder_name_and_metadata(db_session):
    person = Person(
        first_name="IG",
        last_name="Placeholder",
        email="ig-placeholder@example.com",
        display_name="Instagram User 981925791189944",
    )
    db_session.add(person)
    db_session.flush()
    channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.instagram_dm,
        address="981925791189944",
        is_primary=True,
    )
    db_session.add(channel)
    db_session.commit()

    meta_webhooks._persist_instagram_sender_identity(
        person=person,
        channel=channel,
        metadata={
            "sender_id": "981925791189944",
            "sender_username": "real_handle",
            "sender_name": "Real Name",
        },
    )
    db_session.commit()
    db_session.refresh(person)
    db_session.refresh(channel)

    assert person.display_name == "real_handle"
    assert person.metadata_["instagram_profile"]["sender_id"] == "981925791189944"
    assert channel.metadata_["instagram_profile"]["sender_username"] == "real_handle"
    assert person.metadata_["instagram_profile"]["platform"] == "instagram"


def test_persist_instagram_sender_identity_preserves_curated_name(db_session):
    person = Person(
        first_name="Jane",
        last_name="Customer",
        email="jane.customer@example.com",
        display_name="Jane Customer",
    )
    db_session.add(person)
    db_session.flush()
    channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.instagram_dm,
        address="981925791189955",
        is_primary=True,
    )
    db_session.add(channel)
    db_session.commit()

    meta_webhooks._persist_instagram_sender_identity(
        person=person,
        channel=channel,
        metadata={
            "sender_id": "981925791189955",
            "sender_username": "jane_ig",
            "sender_name": "Jane IG",
        },
    )
    db_session.commit()
    db_session.refresh(person)
    db_session.refresh(channel)

    assert person.display_name == "Jane Customer"
    assert channel.metadata_["instagram_profile"]["sender_name"] == "Jane IG"


def test_persist_facebook_sender_identity_updates_placeholder_name_and_metadata(db_session):
    person = Person(
        first_name="FB",
        last_name="Placeholder",
        email="fb-placeholder@example.com",
        display_name="Facebook User 123456",
    )
    db_session.add(person)
    db_session.flush()
    channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.facebook_messenger,
        address="123456",
        is_primary=True,
    )
    db_session.add(channel)
    db_session.commit()

    meta_webhooks._persist_facebook_sender_identity(
        person=person,
        channel=channel,
        metadata={
            "sender_id": "123456",
            "sender_username": "real_fb_handle",
            "sender_name": "Real FB Name",
        },
    )
    db_session.commit()
    db_session.refresh(person)
    db_session.refresh(channel)

    assert person.display_name == "real_fb_handle"
    assert person.metadata_["facebook_profile"]["sender_id"] == "123456"
    assert person.metadata_["facebook_profile"]["platform"] == "facebook"
    assert channel.metadata_["facebook_profile"]["sender_name"] == "Real FB Name"


def test_persist_facebook_sender_identity_preserves_curated_name(db_session):
    person = Person(
        first_name="FB",
        last_name="Customer",
        email="fb-customer@example.com",
        display_name="Jane Customer",
    )
    db_session.add(person)
    db_session.flush()
    channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.facebook_messenger,
        address="123457",
        is_primary=True,
    )
    db_session.add(channel)
    db_session.commit()

    meta_webhooks._persist_facebook_sender_identity(
        person=person,
        channel=channel,
        metadata={
            "sender_id": "123457",
            "sender_username": "fb_jane",
            "sender_name": "FB Jane",
        },
    )
    db_session.commit()
    db_session.refresh(person)
    db_session.refresh(channel)

    assert person.display_name == "Jane Customer"
    assert person.metadata_["facebook_profile"]["sender_username"] == "fb_jane"
    assert channel.metadata_["facebook_profile"]["sender_name"] == "FB Jane"


def test_persist_instagram_sender_identity_ignores_non_instagram_channels(db_session):
    person = Person(
        first_name="Email",
        last_name="Contact",
        email="email-contact@example.com",
        display_name="Facebook User 123",
    )
    db_session.add(person)
    db_session.flush()
    channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.email,
        address="email-contact@example.com",
        is_primary=True,
    )
    db_session.add(channel)
    db_session.commit()

    meta_webhooks._persist_instagram_sender_identity(
        person=person,
        channel=channel,
        metadata={
            "sender_id": "981925791189956",
            "sender_username": "should_not_apply",
            "sender_name": "Should Not Apply",
        },
    )
    db_session.commit()
    db_session.refresh(person)
    db_session.refresh(channel)

    assert person.display_name == "Facebook User 123"
    assert not channel.metadata_


def test_receive_facebook_message_delayed_identity_enrichment_updates_placeholder_contact(db_session, monkeypatch):
    person = Person(
        first_name="Placeholder",
        last_name="Customer",
        email="fb-delayed@example.com",
        display_name="Facebook User 123458",
    )
    db_session.add(person)
    db_session.flush()
    channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.facebook_messenger,
        address="123458",
        is_primary=True,
    )
    db_session.add(channel)
    db_session.commit()

    monkeypatch.setattr(meta_webhooks, "_resolve_meta_connector", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(meta_webhooks, "post_process_inbound_message", lambda *_args, **_kwargs: None)

    payload = FacebookMessengerWebhookPayload(
        contact_address="123458",
        contact_name="Recovered FB Name",
        message_id="fb-reconcile-1",
        page_id="page_123",
        body="hello",
        metadata={
            "sender_id": "123458",
            "sender_username": "recovered_fb",
            "sender_name": "Recovered FB Name",
            "platform": "facebook",
        },
    )

    message = meta_webhooks.receive_facebook_message(db_session, payload)
    db_session.refresh(person)
    db_session.refresh(channel)

    assert message is not None
    assert person.display_name == "recovered_fb"
    assert person.metadata_["facebook_profile"]["sender_name"] == "Recovered FB Name"
    assert channel.metadata_["facebook_profile"]["sender_username"] == "recovered_fb"


def test_receive_facebook_message_preserves_raw_payload_and_attribution(db_session, monkeypatch):
    monkeypatch.setattr(meta_webhooks, "_resolve_meta_connector", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(meta_webhooks, "post_process_inbound_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(meta_webhooks, "_schedule_meta_identity_enrichment", lambda **_kwargs: None)

    raw_event = {
        "sender": {"id": "raw-fb-1"},
        "recipient": {"id": "page_123"},
        "timestamp": 1710000000000,
        "message": {"mid": "raw-mid-1", "text": "hello"},
        "referral": {"source": "ADS", "ad_id": "ad-raw-1", "campaign_id": "camp-raw-1"},
    }
    raw_row = MetaRawEvent(
        platform="facebook_messenger",
        sender_id="raw-fb-1",
        page_id="page_123",
        event_type="message",
        external_message_id="raw-mid-1",
        trace_id="trace-raw-1",
        dedupe_key="dedupe-raw-1",
        raw_payload=raw_event,
        attribution={"source": "ADS", "ad_id": "ad-raw-1", "campaign_id": "camp-raw-1"},
    )
    db_session.add(raw_row)
    db_session.commit()

    payload = FacebookMessengerWebhookPayload(
        contact_address="raw-fb-1",
        contact_name="Recovered FB Name",
        message_id="raw-mid-1",
        page_id="page_123",
        body="hello",
        metadata={
            "raw": raw_event,
            "meta_raw_event_id": str(raw_row.id),
            "sender_id": "raw-fb-1",
            "sender_name": "Recovered FB Name",
            "platform": "facebook",
            "attribution": {"source": "ADS", "ad_id": "ad-raw-1", "campaign_id": "camp-raw-1"},
        },
    )

    message = meta_webhooks.receive_facebook_message(db_session, payload)
    conversation = db_session.query(Conversation).filter(Conversation.id == message.conversation_id).one()

    assert message.metadata_["raw"]["message"]["mid"] == "raw-mid-1"
    assert message.metadata_["meta_raw_event_id"] == str(raw_row.id)
    assert message.metadata_["attribution"]["ad_id"] == "ad-raw-1"
    assert conversation.metadata_["attribution"]["campaign_id"] == "camp-raw-1"


def test_enrich_meta_identity_updates_placeholder_person_without_overwriting_curated_name(db_session, monkeypatch):
    placeholder = Person(
        first_name="Meta",
        last_name="Placeholder",
        email="meta-placeholder-enrich@example.com",
        display_name="Instagram User 1001",
    )
    curated = Person(
        first_name="Curated",
        last_name="Customer",
        email="meta-curated-enrich@example.com",
        display_name="Curated Customer",
    )
    db_session.add_all([placeholder, curated])
    db_session.flush()
    placeholder_channel = PersonChannel(
        person_id=placeholder.id,
        channel_type=PersonChannelType.instagram_dm,
        address="1001",
        is_primary=True,
    )
    curated_channel = PersonChannel(
        person_id=curated.id,
        channel_type=PersonChannelType.instagram_dm,
        address="1002",
        is_primary=True,
    )
    db_session.add_all([placeholder_channel, curated_channel])
    db_session.commit()

    monkeypatch.setattr(meta_webhooks, "_resolve_meta_platform_access_token", lambda *_args, **_kwargs: "token")
    monkeypatch.setattr(
        meta_webhooks,
        "_fetch_profile_identity",
        lambda *_args, **_kwargs: {"sender_username": "real_handle", "sender_name": "Real Name"},
    )

    updated_placeholder = meta_webhooks.enrich_meta_identity(
        db_session, platform="instagram", sender_id="1001", account_id="ig_account_1"
    )
    updated_curated = meta_webhooks.enrich_meta_identity(
        db_session, platform="instagram", sender_id="1002", account_id="ig_account_1"
    )
    db_session.refresh(placeholder)
    db_session.refresh(curated)

    assert updated_placeholder is True
    assert updated_curated is True
    assert placeholder.display_name == "real_handle"
    assert curated.display_name == "Curated Customer"
    assert placeholder.metadata_["instagram_profile"]["sender_name"] == "Real Name"
    assert curated.metadata_["instagram_profile"]["sender_username"] == "real_handle"


def test_receive_instagram_message_schedules_identity_enrichment_when_identity_missing(db_session, monkeypatch):
    scheduled: dict[str, str] = {}

    monkeypatch.setattr(meta_webhooks, "_resolve_meta_connector", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(meta_webhooks, "post_process_inbound_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        meta_webhooks,
        "_schedule_meta_identity_enrichment",
        lambda **kwargs: scheduled.update({k: str(v) for k, v in kwargs.items()}),
    )

    payload = InstagramDMWebhookPayload(
        contact_address="ig-missing-identity-1",
        contact_name="Instagram User ig-missing-identity-1",
        message_id="ig-missing-mid-1",
        instagram_account_id="ig_account_1",
        body="hello",
        metadata={
            "sender_id": "ig-missing-identity-1",
            "platform": "instagram",
            "raw": {"message": {"mid": "ig-missing-mid-1", "text": "hello"}},
        },
    )

    message = meta_webhooks.receive_instagram_message(db_session, payload)

    assert message is not None
    assert scheduled == {
        "platform": "instagram",
        "sender_id": "ig-missing-identity-1",
        "account_id": "ig_account_1",
    }


def test_messenger_referral_without_message_stages_pending_attribution_without_locking_placeholder(
    db_session, monkeypatch
):
    monkeypatch.setattr(meta_webhooks, "_resolve_meta_connector", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(meta_webhooks, "_fetch_profile_name", lambda *_args, **_kwargs: None)

    payload = MetaWebhookPayload(
        object="page",
        entry=[
            {
                "id": "page_123",
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "fb-referral-1"},
                        "recipient": {"id": "page_123"},
                        "timestamp": 1710000000000,
                        "referral": {"source": "ADS", "ad_id": "ad_referral_1", "campaign_id": "camp_1"},
                    }
                ],
            }
        ],
    )

    results = meta_webhooks.process_messenger_webhook(db_session, payload)

    assert results == []
    channel = (
        db_session.query(PersonChannel)
        .filter(PersonChannel.channel_type == PersonChannelType.facebook_messenger)
        .filter(PersonChannel.address == "fb-referral-1")
        .one()
    )
    person = channel.person
    assert channel.metadata_["pending_meta_attribution"]["attribution"]["ad_id"] == "ad_referral_1"
    assert not person.display_name or not person.display_name.startswith("Facebook User")


def test_messenger_pending_attribution_is_applied_to_next_inbound_message(db_session, monkeypatch):
    monkeypatch.setattr(meta_webhooks, "_resolve_meta_connector", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(meta_webhooks, "_fetch_profile_name", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(meta_webhooks, "post_process_inbound_message", lambda *_args, **_kwargs: None)

    referral_payload = MetaWebhookPayload(
        object="page",
        entry=[
            {
                "id": "page_123",
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "fb-referral-2"},
                        "recipient": {"id": "page_123"},
                        "timestamp": 1710000000000,
                        "referral": {"source": "ADS", "ad_id": "ad_referral_2", "campaign_id": "camp_2"},
                    }
                ],
            }
        ],
    )
    meta_webhooks.process_messenger_webhook(db_session, referral_payload)

    message_payload = FacebookMessengerWebhookPayload(
        contact_address="fb-referral-2",
        contact_name="Recovered Name",
        message_id="fb-referral-msg-2",
        page_id="page_123",
        body="hello",
        metadata={
            "sender_id": "fb-referral-2",
            "sender_name": "Recovered Name",
            "platform": "facebook",
        },
    )

    message = meta_webhooks.receive_facebook_message(db_session, message_payload)
    db_session.refresh(message)
    conversation = db_session.query(Conversation).filter(Conversation.id == message.conversation_id).one()
    channel = db_session.query(PersonChannel).filter(PersonChannel.id == message.person_channel_id).one()
    person = channel.person

    assert message.metadata_["attribution"]["ad_id"] == "ad_referral_2"
    assert conversation.metadata_["attribution"]["campaign_id"] == "camp_2"
    assert "pending_meta_attribution" not in (channel.metadata_ or {})
    assert person.display_name == "Recovered Name"


def test_reconcile_meta_identity_for_sender_backfills_pending_attribution_and_placeholder_name(db_session):
    person = Person(
        first_name="Meta",
        last_name="Placeholder",
        email="meta-reconcile@example.com",
        display_name="Facebook User fb-reconcile-3",
    )
    db_session.add(person)
    db_session.flush()
    channel = PersonChannel(
        person_id=person.id,
        channel_type=PersonChannelType.facebook_messenger,
        address="fb-reconcile-3",
        is_primary=True,
        metadata_={
            "facebook_profile": {
                "platform": "facebook",
                "sender_id": "fb-reconcile-3",
                "sender_username": "real_fb_name",
                "sender_name": "Real FB Name",
            },
            "pending_meta_attribution": {
                "page_id": "page_123",
                "captured_at": datetime.now(UTC).isoformat(),
                "attribution": {"source": "ADS", "ad_id": "ad_reconcile_3", "campaign_id": "camp_3"},
            },
        },
    )
    db_session.add(channel)
    db_session.flush()
    conversation = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    db_session.add(conversation)
    db_session.flush()
    message = Message(
        conversation_id=conversation.id,
        person_channel_id=channel.id,
        channel_type=ChannelType.facebook_messenger,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body="hello",
        external_id="fb-reconcile-3-msg",
    )
    db_session.add(message)
    db_session.commit()

    result = meta_webhooks.reconcile_meta_identity_for_sender(db_session, "fb-reconcile-3")
    db_session.refresh(person)
    db_session.refresh(channel)
    db_session.refresh(conversation)
    db_session.refresh(message)

    assert result["pending_consumed"] == 1
    assert result["messages_updated"] == 1
    assert result["conversations_updated"] == 1
    assert person.display_name == "real_fb_name"
    assert message.metadata_["attribution"]["ad_id"] == "ad_reconcile_3"
    assert conversation.metadata_["attribution"]["campaign_id"] == "camp_3"
    assert "pending_meta_attribution" not in (channel.metadata_ or {})


def test_process_facebook_leadgen_change_creates_person_and_lead(db_session):
    _seed_meta_page_token(db_session)
    payload = MetaWebhookPayload(
        object="page",
        entry=[
            {
                "id": "page_123",
                "time": 1,
                "changes": [
                    {
                        "field": "leadgen",
                        "value": {
                            "leadgen_id": "leadgen_1",
                            "form_id": "form_1",
                            "ad_id": "ad_1",
                            "created_time": "2026-03-02T10:00:00+0000",
                        },
                    }
                ],
            }
        ],
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "leadgen_1",
        "created_time": "2026-03-02T10:00:00+0000",
        "form_id": "form_1",
        "ad_id": "ad_1",
        "campaign_id": "campaign_1",
        "platform": "facebook",
        "field_data": [
            {"name": "full_name", "values": ["Jane Lead"]},
            {"name": "email", "values": ["jane@example.com"]},
            {"name": "phone_number", "values": ["+1 (555) 123-4567"]},
            {"name": "city", "values": ["Lagos"]},
            {"name": "state", "values": ["LA"]},
            {"name": "street_address", "values": ["123 Main St"]},
        ],
    }

    with patch("app.services.meta_webhooks.httpx.Client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.get.return_value = mock_response
        mock_client.return_value.__enter__.return_value = mock_instance
        mock_client.return_value.__exit__.return_value = None

        results = meta_webhooks.process_messenger_webhook(db_session, payload)

    assert len(results) == 1
    assert results[0]["leadgen_id"] == "leadgen_1"
    assert results[0]["status"] == "stored"
    stored_lead = db_session.query(Lead).one()
    assert stored_lead.lead_source == "Facebook Ads"
    assert stored_lead.metadata_["meta_leadgen_id"] == "leadgen_1"
    assert stored_lead.metadata_["meta_field_answers"]["email"] == ["jane@example.com"]
    assert stored_lead.person.email == "jane@example.com"
    assert stored_lead.person.phone == "+15551234567"


def test_process_facebook_leadgen_change_is_idempotent(db_session):
    _seed_meta_page_token(db_session)
    payload = MetaWebhookPayload(
        object="page",
        entry=[
            {
                "id": "page_123",
                "time": 1,
                "changes": [{"field": "leadgen", "value": {"leadgen_id": "leadgen_2", "form_id": "form_2"}}],
            }
        ],
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "leadgen_2",
        "created_time": "2026-03-02T10:00:00+0000",
        "form_id": "form_2",
        "platform": "instagram",
        "field_data": [
            {"name": "full_name", "values": ["Ayo Lead"]},
            {"name": "email", "values": ["ayo@example.com"]},
        ],
    }

    with patch("app.services.meta_webhooks.httpx.Client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.get.return_value = mock_response
        mock_client.return_value.__enter__.return_value = mock_instance
        mock_client.return_value.__exit__.return_value = None

        first = meta_webhooks.process_messenger_webhook(db_session, payload)
        second = meta_webhooks.process_messenger_webhook(db_session, payload)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0]["lead_id"] == second[0]["lead_id"]
    assert db_session.query(Lead).count() == 1


def test_process_facebook_leadgen_uses_facebook_override_token(db_session):
    config = ConnectorConfig(name="Meta Connector Override", connector_type=ConnectorType.facebook, is_active=True)
    db_session.add(config)
    db_session.flush()
    db_session.add(
        IntegrationTarget(
            name="CRM",
            target_type=IntegrationTargetType.crm,
            connector_config_id=config.id,
            is_active=True,
        )
    )
    db_session.add(
        DomainSetting(
            domain=SettingDomain.comms,
            key="meta_facebook_access_token_override",
            value_type=SettingValueType.string,
            value_text="override-page-token",
            is_secret=True,
            is_active=True,
        )
    )
    db_session.commit()

    payload = MetaWebhookPayload(
        object="page",
        entry=[
            {
                "id": "page_123",
                "time": 1,
                "changes": [{"field": "leadgen", "value": {"leadgen_id": "leadgen_override_1", "form_id": "form_1"}}],
            }
        ],
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "leadgen_override_1",
        "created_time": "2026-03-02T10:00:00+0000",
        "form_id": "form_1",
        "ad_id": "ad_1",
        "campaign_id": "campaign_1",
        "platform": "facebook",
        "field_data": [
            {"name": "full_name", "values": ["Override Lead"]},
            {"name": "email", "values": ["override@example.com"]},
        ],
    }

    with patch("app.services.meta_webhooks.httpx.Client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.get.return_value = mock_response
        mock_client.return_value.__enter__.return_value = mock_instance
        mock_client.return_value.__exit__.return_value = None

        results = meta_webhooks.process_messenger_webhook(db_session, payload)

    assert len(results) == 1
    assert results[0]["status"] == "stored"
    assert db_session.query(Lead).count() == 1

    called_params = mock_instance.get.call_args.kwargs["params"]
    assert called_params["access_token"] == "override-page-token"


def test_process_whatsapp_webhook_updates_only_whatsapp_outbound_message(db_session):
    person = Person(
        first_name="Test",
        last_name="Contact",
        display_name="Test Contact",
        email="whatsapp-status@example.com",
        is_active=True,
    )
    db_session.add(person)
    db_session.flush()

    whatsapp_conversation = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    email_conversation = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    db_session.add_all([whatsapp_conversation, email_conversation])
    db_session.flush()

    whatsapp_message = Message(
        conversation_id=whatsapp_conversation.id,
        channel_type=ChannelType.whatsapp,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body="WhatsApp outbound",
        external_id="wamid.same",
    )
    email_message = Message(
        conversation_id=email_conversation.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body="Email outbound",
        external_id="wamid.same",
    )
    db_session.add_all([whatsapp_message, email_message])
    db_session.commit()

    payload = MetaWebhookPayload(
        object="whatsapp_business_account",
        entry=[
            {
                "id": "waba_123",
                "time": 1,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.same",
                                    "status": "delivered",
                                    "timestamp": "1712200000",
                                    "recipient_id": "15551234567",
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    )

    with patch("app.websocket.broadcaster.broadcast_message_status") as mock_broadcast:
        results = meta_webhooks.process_whatsapp_webhook(db_session, payload)

    db_session.refresh(whatsapp_message)
    db_session.refresh(email_message)

    assert results == [{"wamid": "wamid.same", "status": "stored"}]
    assert whatsapp_message.status == MessageStatus.delivered
    assert email_message.status == MessageStatus.sent
    mock_broadcast.assert_called_once_with(
        str(whatsapp_message.id),
        str(whatsapp_message.conversation_id),
        MessageStatus.delivered.value,
    )


def test_process_whatsapp_webhook_scopes_by_whatsapp_target_phone_number_id(db_session):
    person = Person(
        first_name="Target",
        last_name="Scoped",
        display_name="Target Scoped",
        email="whatsapp-target-scope@example.com",
        is_active=True,
    )
    db_session.add(person)
    db_session.flush()

    config_a = ConnectorConfig(
        name="WhatsApp Target A",
        connector_type=ConnectorType.whatsapp,
        metadata_={"phone_number_id": "phone-number-a"},
        is_active=True,
    )
    config_b = ConnectorConfig(
        name="WhatsApp Target B",
        connector_type=ConnectorType.whatsapp,
        metadata_={"phone_number_id": "phone-number-b"},
        is_active=True,
    )
    db_session.add_all([config_a, config_b])
    db_session.flush()

    target_a = IntegrationTarget(
        name="Target A",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config_a.id,
        is_active=True,
    )
    target_b = IntegrationTarget(
        name="Target B",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config_b.id,
        is_active=True,
    )
    db_session.add_all([target_a, target_b])
    db_session.flush()

    conversation_a = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    conversation_b = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    db_session.add_all([conversation_a, conversation_b])
    db_session.flush()

    message_a = Message(
        conversation_id=conversation_a.id,
        channel_target_id=target_a.id,
        channel_type=ChannelType.whatsapp,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body="WhatsApp outbound A",
        external_id="wamid.shared",
    )
    message_b = Message(
        conversation_id=conversation_b.id,
        channel_target_id=target_b.id,
        channel_type=ChannelType.whatsapp,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body="WhatsApp outbound B",
        external_id="wamid.shared",
    )
    db_session.add_all([message_a, message_b])
    db_session.commit()

    payload = MetaWebhookPayload(
        object="whatsapp_business_account",
        entry=[
            {
                "id": "waba_123",
                "time": 1,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "phone-number-b"},
                            "statuses": [
                                {
                                    "id": "wamid.shared",
                                    "status": "delivered",
                                    "timestamp": "1712200000",
                                    "recipient_id": "15551234567",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    )

    with patch("app.websocket.broadcaster.broadcast_message_status") as mock_broadcast:
        results = meta_webhooks.process_whatsapp_webhook(db_session, payload)

    db_session.refresh(message_a)
    db_session.refresh(message_b)

    assert results == [{"wamid": "wamid.shared", "status": "stored"}]
    assert message_a.status == MessageStatus.sent
    assert message_b.status == MessageStatus.delivered
    mock_broadcast.assert_called_once_with(
        str(message_b.id),
        str(message_b.conversation_id),
        MessageStatus.delivered.value,
    )


def test_process_whatsapp_webhook_skips_ambiguous_match_without_phone_number_id(db_session):
    person = Person(
        first_name="Ambiguous",
        last_name="Target",
        display_name="Ambiguous Target",
        email="whatsapp-ambiguous@example.com",
        is_active=True,
    )
    db_session.add(person)
    db_session.flush()

    config_a = ConnectorConfig(
        name="WhatsApp Ambiguous A",
        connector_type=ConnectorType.whatsapp,
        metadata_={"phone_number_id": "ambiguous-a"},
        is_active=True,
    )
    config_b = ConnectorConfig(
        name="WhatsApp Ambiguous B",
        connector_type=ConnectorType.whatsapp,
        metadata_={"phone_number_id": "ambiguous-b"},
        is_active=True,
    )
    db_session.add_all([config_a, config_b])
    db_session.flush()

    target_a = IntegrationTarget(
        name="Ambiguous Target A",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config_a.id,
        is_active=True,
    )
    target_b = IntegrationTarget(
        name="Ambiguous Target B",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config_b.id,
        is_active=True,
    )
    db_session.add_all([target_a, target_b])
    db_session.flush()

    conversation_a = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    conversation_b = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    db_session.add_all([conversation_a, conversation_b])
    db_session.flush()

    message_a = Message(
        conversation_id=conversation_a.id,
        channel_target_id=target_a.id,
        channel_type=ChannelType.whatsapp,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body="WhatsApp outbound A",
        external_id="wamid.ambiguous",
    )
    message_b = Message(
        conversation_id=conversation_b.id,
        channel_target_id=target_b.id,
        channel_type=ChannelType.whatsapp,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body="WhatsApp outbound B",
        external_id="wamid.ambiguous",
    )
    db_session.add_all([message_a, message_b])
    db_session.commit()

    payload = MetaWebhookPayload(
        object="whatsapp_business_account",
        entry=[
            {
                "id": "waba_123",
                "time": 1,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.ambiguous",
                                    "status": "delivered",
                                    "timestamp": "1712200000",
                                    "recipient_id": "15551234567",
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    )

    with patch("app.websocket.broadcaster.broadcast_message_status") as mock_broadcast:
        results = meta_webhooks.process_whatsapp_webhook(db_session, payload)

    db_session.refresh(message_a)
    db_session.refresh(message_b)

    assert results == [{"wamid": "wamid.ambiguous", "status": "skipped"}]
    assert message_a.status == MessageStatus.sent
    assert message_b.status == MessageStatus.sent
    mock_broadcast.assert_not_called()
