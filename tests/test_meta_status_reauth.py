"""Tests for meta_status surfacing of expired/refresh-error tokens."""

from datetime import UTC, datetime, timedelta

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.oauth_token import OAuthToken
from app.services.crm.inbox.meta_status import get_meta_connection_status


def _create_meta_target(db_session) -> IntegrationTarget:
    config = ConnectorConfig(name="Meta", connector_type=ConnectorType.facebook)
    db_session.add(config)
    db_session.commit()
    db_session.refresh(config)
    target = IntegrationTarget(
        name="Meta CRM",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db_session.add(target)
    db_session.commit()
    return target


def test_meta_status_flags_expired_token(db_session):
    target = _create_meta_target(db_session)
    db_session.add(
        OAuthToken(
            connector_config_id=target.connector_config_id,
            provider="meta",
            account_type="page",
            external_account_id="page_1",
            external_account_name="Test Page",
            access_token="token",
            token_expires_at=datetime.now(UTC) - timedelta(days=1),
            is_active=True,
        )
    )
    db_session.commit()

    status = get_meta_connection_status(db_session)

    assert status["reauth_required"] is True
    assert status["expired_count"] == 1
    assert status["pages"][0]["is_expired"] is True


def test_meta_status_flags_refresh_error(db_session):
    target = _create_meta_target(db_session)
    db_session.add(
        OAuthToken(
            connector_config_id=target.connector_config_id,
            provider="meta",
            account_type="instagram_business",
            external_account_id="ig_1",
            external_account_name="@brand",
            access_token="token",
            token_expires_at=datetime.now(UTC) + timedelta(days=30),
            refresh_error="some refresh failure",
            is_active=True,
        )
    )
    db_session.commit()

    status = get_meta_connection_status(db_session)

    assert status["reauth_required"] is True
    assert status["expired_count"] == 0
    assert status["instagram_accounts"][0]["has_error"] is True
    assert status["instagram_accounts"][0]["is_expired"] is False


def test_meta_status_healthy_token_no_reauth(db_session):
    target = _create_meta_target(db_session)
    db_session.add(
        OAuthToken(
            connector_config_id=target.connector_config_id,
            provider="meta",
            account_type="page",
            external_account_id="page_1",
            external_account_name="Test Page",
            access_token="token",
            token_expires_at=datetime.now(UTC) + timedelta(days=30),
            is_active=True,
        )
    )
    db_session.commit()

    status = get_meta_connection_status(db_session)

    assert status["reauth_required"] is False
    assert status["expired_count"] == 0
    assert status["pages"][0]["is_expired"] is False
    assert status["pages"][0]["has_error"] is False
