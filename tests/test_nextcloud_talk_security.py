from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.nextcloud_talk import _resolve_client
from app.models.connector import ConnectorAuthType, ConnectorConfig, ConnectorType
from app.schemas.nextcloud_talk import NextcloudTalkLoginRequest, NextcloudTalkRoomListRequest
from app.schemas.settings import DomainSettingUpdate
from app.services import nextcloud_talk_notifications, settings_api
from app.services.nextcloud_talk import NextcloudTalkError, normalize_and_validate_nextcloud_base_url


def _add_talk_connector(db_session, *, timeout_sec=None) -> ConnectorConfig:
    config = ConnectorConfig(
        name="Talk Connector Security Test",
        connector_type=ConnectorType.custom,
        base_url="https://cloud.example.com/",
        auth_type=ConnectorAuthType.basic,
        auth_config={
            "username": "talk-user",
            "app_password": "talk-secret",
            "timeout_sec": timeout_sec,
        },
        timeout_sec=None,
        is_active=True,
    )
    db_session.add(config)
    db_session.commit()
    db_session.refresh(config)
    return config


def test_normalize_nextcloud_base_url_removes_path_and_trailing_slash():
    normalized = normalize_and_validate_nextcloud_base_url("https://cloud.example.com/ocs/v2.php/")
    assert normalized == "https://cloud.example.com"


def test_normalize_nextcloud_base_url_rejects_local_targets():
    with pytest.raises(NextcloudTalkError, match="Local/loopback hostnames are not allowed"):
        normalize_and_validate_nextcloud_base_url("http://localhost")

    with pytest.raises(NextcloudTalkError, match="Private or local network addresses are not allowed"):
        normalize_and_validate_nextcloud_base_url("http://127.0.0.1")


def test_login_schema_rejects_localhost_url():
    with pytest.raises(ValidationError, match="Local/loopback hostnames are not allowed"):
        NextcloudTalkLoginRequest(
            base_url="http://localhost",
            username="talk-user",
            app_password="secret",
        )


def test_auth_schema_normalizes_base_url():
    payload = NextcloudTalkRoomListRequest(
        base_url="https://cloud.example.com/ocs/",
        username="talk-user",
        app_password="secret",
    )
    assert payload.base_url == "https://cloud.example.com"


def test_resolve_client_denies_connector_for_non_admin(db_session):
    config = _add_talk_connector(db_session)
    payload = SimpleNamespace(
        connector_config_id=str(config.id),
        base_url=None,
        username=None,
        app_password=None,
        timeout_sec=None,
    )
    with pytest.raises(HTTPException) as exc:
        _resolve_client(db_session, payload, auth={"roles": ["user"], "scopes": []})
    assert exc.value.status_code == 403


def test_resolve_client_accepts_admin_and_falls_back_timeout(db_session):
    config = _add_talk_connector(db_session, timeout_sec="bad-timeout")
    payload = SimpleNamespace(
        connector_config_id=str(config.id),
        base_url=None,
        username=None,
        app_password=None,
        timeout_sec=None,
    )

    client = _resolve_client(
        db_session,
        payload,
        auth={"roles": [], "scopes": ["system:settings:read"]},
    )
    assert client.base_url == "https://cloud.example.com"
    assert client.timeout == 30.0


def test_notification_config_ignores_invalid_base_url(db_session):
    settings_api.upsert_notification_setting(
        db_session,
        "nextcloud_talk_notifications_enabled",
        DomainSettingUpdate(value_json=True),
    )
    settings_api.upsert_notification_setting(
        db_session,
        "nextcloud_talk_notifications_base_url",
        DomainSettingUpdate(value_text="http://127.0.0.1"),
    )
    settings_api.upsert_notification_setting(
        db_session,
        "nextcloud_talk_notifications_username",
        DomainSettingUpdate(value_text="Dotmac Notifications"),
    )
    settings_api.upsert_notification_setting(
        db_session,
        "nextcloud_talk_notifications_app_password",
        DomainSettingUpdate(value_text="secret"),
    )

    assert nextcloud_talk_notifications._resolve_notification_config(db_session) is None
