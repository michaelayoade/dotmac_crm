from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.sales import Lead
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.oauth_token import OAuthToken
from app.schemas.crm.inbox import MetaWebhookPayload
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
