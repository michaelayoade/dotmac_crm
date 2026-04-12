"""Verify that expired OAuth tokens fan out an in-app push notification to
all active CRM agents in addition to the email alert."""

from datetime import UTC, datetime, timedelta

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.team import CrmAgent
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.notification import Notification, NotificationChannel
from app.models.oauth_token import OAuthToken
from app.tasks import webhook_health


def _create_meta_target(db_session) -> IntegrationTarget:
    config = ConnectorConfig(name="Meta", connector_type=ConnectorType.facebook)
    db_session.add(config)
    db_session.commit()
    target = IntegrationTarget(
        name="Meta CRM",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db_session.add(target)
    db_session.commit()
    return target


def test_expired_token_fans_out_push_notification_to_active_agents(monkeypatch, db_session, person):
    target = _create_meta_target(db_session)
    db_session.add(
        OAuthToken(
            connector_config_id=target.connector_config_id,
            provider="meta",
            account_type="instagram_business",
            external_account_id="ig_1",
            external_account_name="@brand",
            access_token="token",
            token_expires_at=datetime.now(UTC) - timedelta(hours=1),
            is_active=True,
        )
    )
    db_session.add(CrmAgent(person_id=person.id, is_active=True))
    db_session.commit()

    monkeypatch.setattr(webhook_health, "_was_recently_alerted", lambda *a, **kw: False)

    issues = webhook_health._check_token_expiry(db_session, recipient="ops@example.test")
    db_session.flush()

    assert any("EXPIRED" in i for i in issues)
    pushes = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == str(person.id))
        .all()
    )
    assert len(pushes) == 1
    assert "Reconnect required" in pushes[0].subject
    assert "@brand" in pushes[0].subject


def test_expiring_soon_token_does_not_fan_out_push(monkeypatch, db_session, person):
    target = _create_meta_target(db_session)
    db_session.add(
        OAuthToken(
            connector_config_id=target.connector_config_id,
            provider="meta",
            account_type="instagram_business",
            external_account_id="ig_2",
            external_account_name="@later",
            access_token="token",
            token_expires_at=datetime.now(UTC) + timedelta(days=3),
            is_active=True,
        )
    )
    db_session.add(CrmAgent(person_id=person.id, is_active=True))
    db_session.commit()

    monkeypatch.setattr(webhook_health, "_was_recently_alerted", lambda *a, **kw: False)

    webhook_health._check_token_expiry(db_session, recipient="ops@example.test")
    db_session.flush()

    pushes = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == str(person.id))
        .all()
    )
    assert pushes == []
