from unittest.mock import MagicMock, patch

from app.services import meta_webhooks


def test_normalize_external_id_and_ref():
    short = meta_webhooks._normalize_external_id("abc")
    assert short == "abc"
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
