"""Tests for the webhook channel health check task."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.crm.conversation import Message
from app.models.crm.enums import ChannelType, MessageDirection, MessageStatus
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.webhook_dead_letter import WebhookDeadLetter
from app.tasks.webhook_health import (
    ALERT_COOLDOWN_MINUTES,
    CHANNEL_SILENCE_THRESHOLDS,
    DEAD_LETTER_LOOKBACK_MINUTES,
    DEAD_LETTER_THRESHOLD,
    _check_channel_silence,
    _check_dead_letters,
    _check_outbox_stuck,
    _was_recently_alerted,
)

FAKE_RECIPIENT = "ops@dotmac.ng"


def _make_inbound_message(db, channel_type, created_at):
    """Helper to create a minimal inbound message for testing."""
    conv_id = uuid.uuid4()
    msg = Message(
        id=uuid.uuid4(),
        conversation_id=conv_id,
        channel_type=channel_type,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        created_at=created_at,
    )
    db.add(msg)
    db.flush()
    return msg


def _make_dead_letter(db, channel, created_at):
    """Helper to create a dead letter entry."""
    dl = WebhookDeadLetter(
        id=uuid.uuid4(),
        channel=channel,
        raw_payload={"test": True},
        error="test error",
        created_at=created_at,
    )
    db.add(dl)
    db.flush()
    return dl


class TestChannelSilence:
    def test_detects_silent_whatsapp_channel(self, db_session):
        """Alert when no WhatsApp messages received within threshold."""
        # Place a message well outside the threshold
        old_time = datetime.now(UTC) - timedelta(hours=24)
        _make_inbound_message(db_session, ChannelType.whatsapp, old_time)

        issues = _check_channel_silence(db_session, FAKE_RECIPIENT)

        whatsapp_issues = [i for i in issues if "whatsapp" in i]
        assert len(whatsapp_issues) >= 1
        assert "silent" in whatsapp_issues[0]

    def test_no_alert_when_recent_messages(self, db_session):
        """No alert when messages are recent."""
        recent_time = datetime.now(UTC) - timedelta(minutes=5)
        _make_inbound_message(db_session, ChannelType.whatsapp, recent_time)

        issues = _check_channel_silence(db_session, FAKE_RECIPIENT)

        whatsapp_issues = [i for i in issues if "whatsapp" in i]
        assert len(whatsapp_issues) == 0

    def test_creates_notification_for_silent_channel(self, db_session):
        """A Notification record is queued when a channel goes silent."""
        old_time = datetime.now(UTC) - timedelta(hours=24)
        _make_inbound_message(db_session, ChannelType.whatsapp, old_time)

        _check_channel_silence(db_session, FAKE_RECIPIENT)

        notifications = (
            db_session.query(Notification)
            .filter(Notification.subject.contains("[Channel Silent]"))
            .filter(Notification.subject.contains("whatsapp"))
            .all()
        )
        assert len(notifications) >= 1
        assert notifications[0].recipient == FAKE_RECIPIENT
        assert notifications[0].status == NotificationStatus.queued


class TestDeadLetters:
    def test_detects_dead_letter_accumulation(self, db_session):
        """Alert when dead letters exceed threshold."""
        now = datetime.now(UTC)
        for i in range(DEAD_LETTER_THRESHOLD + 1):
            _make_dead_letter(db_session, "whatsapp", now - timedelta(minutes=i))

        issues = _check_dead_letters(db_session, FAKE_RECIPIENT)

        assert len(issues) >= 1
        assert "dead letters" in issues[0]

    def test_no_alert_below_threshold(self, db_session):
        """No alert when dead letters are below threshold."""
        now = datetime.now(UTC)
        for i in range(DEAD_LETTER_THRESHOLD - 1):
            _make_dead_letter(db_session, "whatsapp", now - timedelta(minutes=i))

        issues = _check_dead_letters(db_session, FAKE_RECIPIENT)

        assert len(issues) == 0

    def test_ignores_old_dead_letters(self, db_session):
        """Dead letters outside the lookback window are ignored."""
        old_time = datetime.now(UTC) - timedelta(minutes=DEAD_LETTER_LOOKBACK_MINUTES + 30)
        for i in range(DEAD_LETTER_THRESHOLD + 5):
            _make_dead_letter(db_session, "whatsapp", old_time)

        issues = _check_dead_letters(db_session, FAKE_RECIPIENT)

        assert len(issues) == 0


class TestAlertCooldown:
    def test_suppresses_duplicate_alerts(self, db_session):
        """Don't re-alert for the same issue within cooldown period."""
        # Create a recent alert notification
        notification = Notification(
            channel=NotificationChannel.email,
            recipient=FAKE_RECIPIENT,
            subject="[Channel Silent] whatsapp",
            body="test",
            status=NotificationStatus.queued,
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        db_session.add(notification)
        db_session.flush()

        assert _was_recently_alerted(db_session, "[Channel Silent] whatsapp") is True

    def test_allows_alert_after_cooldown(self, db_session):
        """Allow re-alerting after the cooldown period expires."""
        notification = Notification(
            channel=NotificationChannel.email,
            recipient=FAKE_RECIPIENT,
            subject="[Channel Silent] whatsapp",
            body="test",
            status=NotificationStatus.queued,
            created_at=datetime.now(UTC) - timedelta(minutes=ALERT_COOLDOWN_MINUTES + 10),
        )
        db_session.add(notification)
        db_session.flush()

        assert _was_recently_alerted(db_session, "[Channel Silent] whatsapp") is False


class TestOutboxStuck:
    def test_detects_stuck_outbox_messages(self, db_session):
        """Alert when outbox messages are stuck in sending state."""
        from app.models.crm.outbox import OutboxMessage

        stuck_msg = OutboxMessage(
            id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            channel_type=ChannelType.whatsapp,
            status="sending",
            created_at=datetime.now(UTC) - timedelta(hours=2),
            payload={},
        )
        db_session.add(stuck_msg)
        db_session.flush()

        issues = _check_outbox_stuck(db_session, FAKE_RECIPIENT)

        assert len(issues) >= 1
        assert "stuck" in issues[0]
