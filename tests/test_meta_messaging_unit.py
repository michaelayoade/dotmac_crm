from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.enums import ChannelType
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.oauth_token import OAuthToken
from app.services import meta_messaging


def _create_target(db_session):
    config = ConnectorConfig(name="Meta Connector", connector_type=ConnectorType.facebook)
    db_session.add(config)
    db_session.commit()
    target = IntegrationTarget(
        name="Meta Target",
        connector_config_id=config.id,
        target_type=IntegrationTargetType.crm,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()
    return config, target


@pytest.mark.asyncio
async def test_send_facebook_message_success(db_session):
    config, target = _create_target(db_session)
    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="token",
        scopes=["pages_messaging"],
        token_expires_at=datetime.now(UTC) + timedelta(days=30),
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"message_id": "m1", "recipient_id": "u1"}
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.meta_messaging.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await meta_messaging.send_facebook_message(
            db_session, "u1", "Hello", target=target
        )

    assert result["message_id"] == "m1"


def test_get_token_for_channel(db_session):
    config, target = _create_target(db_session)
    token = OAuthToken(
        connector_config_id=config.id,
        provider="meta",
        account_type="page",
        external_account_id="page_123",
        access_token="token",
        scopes=["pages_messaging"],
        is_active=True,
    )
    db_session.add(token)
    db_session.commit()

    found = meta_messaging._get_token_for_channel(
        db_session, ChannelType.facebook_messenger, target
    )
    assert found is not None
    assert found.access_token == "token"
