"""Tests for Meta messaging service."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.enums import ChannelType
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.oauth_token import OAuthToken
from app.services import meta_messaging


# =============================================================================
# Send Facebook Message Tests
# =============================================================================


@pytest.mark.asyncio
async def test_send_facebook_message_success(db_session):
    """Test sending a Facebook Messenger message successfully."""
    # Create connector and target
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="test_token",
        scopes=["pages_messaging"],
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "message_id": "m_abc123",
        "recipient_id": "user_456",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await meta_messaging.send_facebook_message(
            db=db_session,
            recipient_psid="user_456",
            message_text="Hello!",
            target=target,
        )

        assert result["message_id"] == "m_abc123"
        assert result["recipient_id"] == "user_456"


@pytest.mark.asyncio
async def test_send_facebook_message_no_token(db_session):
    """Test sending message fails when no token available."""
    # Create connector without token
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    with pytest.raises(ValueError, match="No active Facebook Page token found"):
        await meta_messaging.send_facebook_message(
            db=db_session,
            recipient_psid="user_456",
            message_text="Hello!",
            target=target,
        )


@pytest.mark.asyncio
async def test_send_facebook_message_expired_token(db_session):
    """Test sending message fails with expired token."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    # Create expired token
    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        external_account_name="Test Page",
        access_token="expired_token",
        scopes=["pages_messaging"],
        token_expires_at=datetime.now(timezone.utc) - timedelta(days=1),  # Expired
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    with pytest.raises(ValueError, match="has expired"):
        await meta_messaging.send_facebook_message(
            db=db_session,
            recipient_psid="user_456",
            message_text="Hello!",
            target=target,
        )


@pytest.mark.asyncio
async def test_send_facebook_message_http_error(db_session):
    """Test sending message handles HTTP errors."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="test_token",
        scopes=["pages_messaging"],
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=MagicMock()
        )
        mock_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        with pytest.raises(httpx.HTTPStatusError):
            await meta_messaging.send_facebook_message(
                db=db_session,
                recipient_psid="user_456",
                message_text="Hello!",
                target=target,
            )


@pytest.mark.asyncio
async def test_send_facebook_message_inactive_token(db_session):
    """Test sending message fails when token is inactive."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    # Create inactive token
    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="inactive_token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=False,  # Inactive
    )
    db_session.add(token)
    db_session.commit()

    with pytest.raises(ValueError, match="No active Facebook Page token found"):
        await meta_messaging.send_facebook_message(
            db=db_session,
            recipient_psid="user_456",
            message_text="Hello!",
            target=target,
        )


@pytest.mark.asyncio
async def test_send_facebook_message_rate_limited_retries(db_session):
    """Test retry on 429 rate limit."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Meta CRM Target",
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="test_token",
        scopes=["pages_messaging"],
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "0"}
    resp_429.text = "rate limited"
    resp_429.raise_for_status = MagicMock()

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.headers = {}
    resp_200.json.return_value = {"message_id": "m_retry", "recipient_id": "user_456"}
    resp_200.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=[resp_429, resp_200])
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await meta_messaging.send_facebook_message(
            db=db_session,
            recipient_psid="user_456",
            message_text="Hello!",
            target=target,
        )

    assert result["message_id"] == "m_retry"
    assert mock_instance.post.call_count == 2


@pytest.mark.asyncio
async def test_send_facebook_message_missing_scope(db_session):
    """Test sending message fails when required scopes are missing."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Meta CRM Target",
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="test_token",
        scopes=["pages_read_engagement"],
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    with pytest.raises(ValueError, match="Missing required Meta permissions"):
        await meta_messaging.send_facebook_message(
            db=db_session,
            recipient_psid="user_456",
            message_text="Hello!",
            target=target,
        )


# =============================================================================
# Send Instagram Message Tests
# =============================================================================


@pytest.mark.asyncio
async def test_send_instagram_message_success(db_session):
    """Test sending an Instagram DM successfully."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="instagram_business",
        external_account_id="ig_123",
        access_token="test_ig_token",
        scopes=["instagram_manage_messages"],
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "message_id": "ig_m_xyz789",
        "recipient_id": "ig_user_456",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await meta_messaging.send_instagram_message(
            db=db_session,
            recipient_igsid="ig_user_456",
            message_text="Hello from Instagram!",
            target=target,
        )

        assert result["message_id"] == "ig_m_xyz789"
        assert result["recipient_id"] == "ig_user_456"


@pytest.mark.asyncio
async def test_send_instagram_message_no_token(db_session):
    """Test sending Instagram message fails when no token available."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    with pytest.raises(ValueError, match="No active Instagram Business Account token found"):
        await meta_messaging.send_instagram_message(
            db=db_session,
            recipient_igsid="ig_user_456",
            message_text="Hello!",
            target=target,
        )


@pytest.mark.asyncio
async def test_send_instagram_message_expired_token(db_session):
    """Test sending Instagram message fails with expired token."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    # Create expired token
    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="instagram_business",
        external_account_id="ig_123",
        external_account_name="Test IG Account",
        access_token="expired_token",
        scopes=["instagram_manage_messages"],
        token_expires_at=datetime.now(timezone.utc) - timedelta(days=1),  # Expired
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    with pytest.raises(ValueError, match="has expired"):
        await meta_messaging.send_instagram_message(
            db=db_session,
            recipient_igsid="ig_user_456",
            message_text="Hello!",
            target=target,
        )


@pytest.mark.asyncio
async def test_send_instagram_message_rate_limited_retries(db_session):
    """Test retry on 429 rate limit for Instagram."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Meta CRM Target",
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="instagram_business",
        external_account_id="ig_123",
        access_token="test_ig_token",
        scopes=["instagram_manage_messages"],
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "0"}
    resp_429.text = "rate limited"
    resp_429.raise_for_status = MagicMock()

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.headers = {}
    resp_200.json.return_value = {"message_id": "ig_retry", "recipient_id": "ig_user_456"}
    resp_200.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=[resp_429, resp_200])
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await meta_messaging.send_instagram_message(
            db=db_session,
            recipient_igsid="ig_user_456",
            message_text="Hello!",
            target=target,
        )

    assert result["message_id"] == "ig_retry"
    assert mock_instance.post.call_count == 2


@pytest.mark.asyncio
async def test_send_instagram_message_missing_scope(db_session):
    """Test sending Instagram message fails when required scopes are missing."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Meta CRM Target",
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="instagram_business",
        external_account_id="ig_123",
        access_token="test_ig_token",
        scopes=["instagram_basic"],
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    with pytest.raises(ValueError, match="Missing required Meta permissions"):
        await meta_messaging.send_instagram_message(
            db=db_session,
            recipient_igsid="ig_user_456",
            message_text="Hello!",
            target=target,
        )


# =============================================================================
# Sync Wrapper Tests
# =============================================================================


def test_send_facebook_message_sync(db_session):
    """Test synchronous wrapper for sending Facebook messages."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="test_token",
        scopes=["pages_messaging"],
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "message_id": "m_sync_123",
        "recipient_id": "user_456",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = meta_messaging.send_facebook_message_sync(
            db=db_session,
            recipient_psid="user_456",
            message_text="Hello sync!",
            target=target,
        )

        assert result["message_id"] == "m_sync_123"


def test_send_instagram_message_sync(db_session):
    """Test synchronous wrapper for sending Instagram messages."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="instagram_business",
        external_account_id="ig_123",
        access_token="test_ig_token",
        scopes=["instagram_manage_messages"],
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "message_id": "ig_m_sync_123",
        "recipient_id": "ig_user_456",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = meta_messaging.send_instagram_message_sync(
            db=db_session,
            recipient_igsid="ig_user_456",
            message_text="Hello sync!",
            target=target,
        )

        assert result["message_id"] == "ig_m_sync_123"


# =============================================================================
# Helper Function Tests
# =============================================================================


def test_get_token_for_channel_facebook(db_session):
    """Test getting token for Facebook Messenger channel."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="test_token",
        scopes=["pages_messaging"],
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    result = meta_messaging._get_token_for_channel(
        db_session, ChannelType.facebook_messenger, target
    )

    assert result is not None
    assert result.access_token == "test_token"


def test_get_token_for_channel_instagram(db_session):
    """Test getting token for Instagram DM channel."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="instagram_business",
        external_account_id="ig_123",
        access_token="test_ig_token",
        scopes=["instagram_manage_messages"],
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    result = meta_messaging._get_token_for_channel(
        db_session, ChannelType.instagram_dm, target
    )

    assert result is not None
    assert result.access_token == "test_ig_token"


def test_get_token_for_channel_with_account_id(db_session):
    """Test getting token selects the requested account."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Meta CRM Target",
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token_one = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="token_one",
        scopes=["pages_messaging"],
        is_active=True,
    )
    token_two = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_456",
        access_token="token_two",
        scopes=["pages_messaging"],
        is_active=True,
    )
    db_session.add_all([token_one, token_two])
    db_session.commit()

    result = meta_messaging._get_token_for_channel(
        db_session,
        ChannelType.facebook_messenger,
        target,
        account_id="page_456",
    )

    assert result is not None
    assert result.access_token == "token_two"


def test_get_token_for_channel_no_target(db_session):
    """Test getting token returns None when no target."""
    result = meta_messaging._get_token_for_channel(
        db_session, ChannelType.facebook_messenger, None
    )
    assert result is None


def test_get_any_page_token(db_session):
    """Test getting any page token for connector."""
    config = ConnectorConfig(
        name="Meta Connector",
        connector_type=ConnectorType.facebook,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(name="Meta CRM Target", 
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)

    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="any_page_token",
        scopes=["pages_messaging"],
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    result = meta_messaging._get_any_page_token(db_session, target)

    assert result is not None
    assert result.access_token == "any_page_token"
