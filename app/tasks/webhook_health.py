"""Celery task for monitoring webhook channel health.

Runs periodically to detect:
- Channels that have gone silent (no inbound messages)
- Dead letter queue accumulation
- Expired or soon-to-expire Meta/Instagram tokens
- Webhook endpoint error spikes

Alerts are sent via the Notification model (queued email).
"""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, text

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging import get_logger
from app.metrics import observe_job
from app.models.crm.conversation import Message
from app.models.crm.enums import ChannelType, MessageDirection
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.webhook_dead_letter import WebhookDeadLetter

logger = get_logger(__name__)

# Channels to monitor and their maximum allowed silence (in minutes)
CHANNEL_SILENCE_THRESHOLDS = {
    ChannelType.whatsapp: 120,  # 2 hours
    ChannelType.email: 240,  # 4 hours
    ChannelType.instagram_dm: 360,  # 6 hours
    ChannelType.facebook_messenger: 360,  # 6 hours
}

# Dead letter threshold: alert if more than this many in the lookback window
DEAD_LETTER_THRESHOLD = 5
DEAD_LETTER_LOOKBACK_MINUTES = 60

# Token expiry warning threshold
TOKEN_EXPIRY_WARNING_DAYS = 7

# Cooldown: don't re-alert for the same issue within this period
ALERT_COOLDOWN_MINUTES = 120


def _get_alert_recipient(session) -> str | None:
    """Resolve the alert recipient email from domain_settings."""
    row = session.execute(
        text("SELECT value_text FROM domain_settings WHERE key = 'webhook_health_alert_email'")
    ).scalar()
    if row:
        return row
    # Fallback: try the NOC email connector
    row = session.execute(
        text("""
            SELECT cc.auth_config::json->>'from_email'
            FROM connector_configs cc
            WHERE cc.name = 'NOC Mail' AND cc.is_active = true
            LIMIT 1
        """)
    ).scalar()
    return row


def _was_recently_alerted(session, alert_key: str) -> bool:
    """Check if we already sent an alert for this key within the cooldown period."""
    cutoff = datetime.now(UTC) - timedelta(minutes=ALERT_COOLDOWN_MINUTES)
    existing = (
        session.query(Notification)
        .filter(Notification.subject.contains(alert_key))
        .filter(Notification.created_at >= cutoff)
        .first()
    )
    return existing is not None


def _send_alert(session, recipient: str, subject: str, body: str) -> None:
    """Queue an email alert notification."""
    notification = Notification(
        channel=NotificationChannel.email,
        recipient=recipient,
        subject=subject,
        body=body,
        status=NotificationStatus.queued,
    )
    session.add(notification)
    session.flush()
    logger.warning("webhook_health_alert subject=%s recipient=%s", subject, recipient)


def _fanout_admin_push(session, subject: str, body: str) -> int:
    """Fan out a push notification to all active CRM agents.

    Used for actionable alerts (e.g. expired OAuth tokens) that need to land in
    the in-app notification dropdown so admins notice without checking email.
    Returns the number of push notifications created.
    """
    from app.models.crm.team import CrmAgent

    agent_person_ids = (
        session.query(CrmAgent.person_id)
        .filter(CrmAgent.is_active.is_(True))
        .filter(CrmAgent.person_id.isnot(None))
        .all()
    )
    created = 0
    for (person_id,) in agent_person_ids:
        session.add(
            Notification(
                channel=NotificationChannel.push,
                recipient=str(person_id),
                subject=subject,
                body=body,
                status=NotificationStatus.delivered,
                sent_at=datetime.now(UTC),
            )
        )
        created += 1
    if created:
        session.flush()
    return created


def _check_channel_silence(session, recipient: str) -> list[str]:
    """Detect channels with no inbound messages beyond their silence threshold."""
    issues = []
    now = datetime.now(UTC)

    for channel_type, max_silence_minutes in CHANNEL_SILENCE_THRESHOLDS.items():
        cutoff = now - timedelta(minutes=max_silence_minutes)

        last_inbound = (
            session.query(func.max(Message.created_at))
            .filter(Message.channel_type == channel_type)
            .filter(Message.direction == MessageDirection.inbound)
            .filter(Message.created_at >= cutoff)
            .scalar()
        )

        if last_inbound is None:
            # No inbound message within the threshold window
            # Find the actual last message to report how long it's been
            actual_last = (
                session.query(func.max(Message.created_at))
                .filter(Message.channel_type == channel_type)
                .filter(Message.direction == MessageDirection.inbound)
                .scalar()
            )

            if actual_last is None:
                silence_desc = "no messages ever recorded"
            else:
                # Ensure timezone-aware for comparison (SQLite returns naive)
                if actual_last.tzinfo is None:
                    actual_last = actual_last.replace(tzinfo=UTC)
                hours_ago = (now - actual_last).total_seconds() / 3600
                silence_desc = f"last message {hours_ago:.1f} hours ago ({actual_last.strftime('%Y-%m-%d %H:%M')} UTC)"

            alert_key = f"[Channel Silent] {channel_type.value}"
            if not _was_recently_alerted(session, alert_key):
                issue = f"{channel_type.value}: silent for >{max_silence_minutes}min — {silence_desc}"
                issues.append(issue)
                _send_alert(
                    session,
                    recipient,
                    alert_key,
                    f"No inbound {channel_type.value} messages received in the last "
                    f"{max_silence_minutes} minutes.\n\n{silence_desc}\n\n"
                    f"Possible causes:\n"
                    f"- Webhook signature verification failing (check META_APP_SECRET)\n"
                    f"- Access token expired\n"
                    f"- Meta/provider stopped sending (persistent errors)\n"
                    f"- Rate limiting (429 responses)\n\n"
                    f"Check: docker logs dotmac_omni_app | grep '/webhooks/crm/'",
                )

    return issues


def _check_dead_letters(session, recipient: str) -> list[str]:
    """Detect dead letter queue accumulation."""
    issues = []
    cutoff = datetime.now(UTC) - timedelta(minutes=DEAD_LETTER_LOOKBACK_MINUTES)

    dead_letter_counts = (
        session.query(
            WebhookDeadLetter.channel,
            func.count(WebhookDeadLetter.id).label("count"),
        )
        .filter(WebhookDeadLetter.created_at >= cutoff)
        .group_by(WebhookDeadLetter.channel)
        .all()
    )

    for channel, count in dead_letter_counts:
        if count >= DEAD_LETTER_THRESHOLD:
            alert_key = f"[Dead Letters] {channel}"
            if not _was_recently_alerted(session, alert_key):
                issue = f"{channel}: {count} dead letters in last {DEAD_LETTER_LOOKBACK_MINUTES}min"
                issues.append(issue)
                _send_alert(
                    session,
                    recipient,
                    alert_key,
                    f"{count} webhook messages failed processing on the {channel} channel "
                    f"in the last {DEAD_LETTER_LOOKBACK_MINUTES} minutes.\n\n"
                    f"These messages are stored in the webhook_dead_letters table "
                    f"and may be recoverable.\n\n"
                    f"Check recent dead-letter entries for this channel in admin tools "
                    f"and review the latest error details.",
                )

    return issues


def _check_token_expiry(session, recipient: str) -> list[str]:
    """Check for expired or soon-to-expire OAuth tokens and access tokens."""
    issues = []
    now = datetime.now(UTC)

    # Check OAuth tokens table
    from app.models.oauth_token import OAuthToken

    expiring_tokens = (
        session.query(OAuthToken)
        .filter(OAuthToken.is_active.is_(True))
        .filter(OAuthToken.token_expires_at.isnot(None))
        .filter(OAuthToken.token_expires_at <= now + timedelta(days=TOKEN_EXPIRY_WARNING_DAYS))
        .all()
    )

    for token in expiring_tokens:
        expires_at = token.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        is_expired = expires_at <= now
        status_word = "EXPIRED" if is_expired else "expiring soon"

        alert_key = f"[Token {status_word.split()[0]}] {token.provider}:{token.external_account_name}"
        if not _was_recently_alerted(session, alert_key):
            issue = f"{token.provider}/{token.external_account_name}: {status_word} ({token.token_expires_at.strftime('%Y-%m-%d %H:%M')} UTC)"
            issues.append(issue)
            email_body = (
                f"OAuth token for {token.provider} account '{token.external_account_name}' "
                f"is {status_word}.\n\n"
                f"Expires: {token.token_expires_at.strftime('%Y-%m-%d %H:%M')} UTC\n"
                f"{'This token has already expired and the integration is broken.' if is_expired else 'Refresh this token before it expires to avoid service disruption.'}\n\n"
                f"Refresh via: CRM Admin → Settings → Meta OAuth"
            )
            _send_alert(session, recipient, alert_key, email_body)
            # Already-expired tokens cannot be auto-refreshed by the scheduler;
            # surface them in the in-app dropdown so an admin notices fast.
            if is_expired:
                _fanout_admin_push(
                    session,
                    subject=f"Reconnect required: {token.provider} {token.external_account_name}",
                    body=(
                        f"{token.provider.title()} token for "
                        f"{token.external_account_name} has expired and cannot be "
                        f"auto-refreshed.\n"
                        f"Open: /admin/crm/inbox/settings"
                    ),
                )

    # Check domain_settings access token overrides by testing the Meta Graph API
    _check_meta_token_health(session, recipient, issues)

    return issues


def _check_meta_token_health(session, recipient: str, issues: list[str]) -> None:
    """Verify Meta access tokens are functional by making a lightweight API call."""
    import httpx

    token_keys = [
        ("meta_facebook_access_token_override", "Facebook Page"),
        ("meta_instagram_access_token_override", "Instagram"),
    ]

    for setting_key, label in token_keys:
        token = session.execute(
            text("SELECT value_text FROM domain_settings WHERE key = :key"),
            {"key": setting_key},
        ).scalar()

        if not token:
            continue

        try:
            resp = httpx.get(
                "https://graph.facebook.com/v21.0/me",
                params={"access_token": token},
                timeout=10,
            )
            if resp.status_code != 200:
                error_data = resp.json().get("error", {})
                error_msg = error_data.get("message", resp.text[:200])

                alert_key = f"[Token Invalid] {label}"
                if not _was_recently_alerted(session, alert_key):
                    issue = f"{label} token: invalid — {error_msg}"
                    issues.append(issue)
                    _send_alert(
                        session,
                        recipient,
                        alert_key,
                        f"The {label} access token ({setting_key}) is not working.\n\n"
                        f"API response: {error_msg}\n\n"
                        f"This means inbound {label} messages may not be processed "
                        f"and outbound messages will fail.\n\n"
                        f"Refresh the token in the Meta Developer Dashboard and update "
                        f"the domain_settings value.",
                    )
        except httpx.RequestError as exc:
            logger.warning("webhook_health_token_check_failed key=%s error=%s", setting_key, exc)


def _check_outbox_stuck(session, recipient: str) -> list[str]:
    """Detect outbox messages stuck in 'sending' state."""
    issues = []
    cutoff = datetime.now(UTC) - timedelta(hours=1)

    from app.models.crm.outbox import OutboxMessage

    stuck_count = (
        session.query(func.count(OutboxMessage.id))
        .filter(OutboxMessage.status == "sending")
        .filter(OutboxMessage.created_at < cutoff)
        .scalar()
    )

    if stuck_count and stuck_count > 0:
        alert_key = "[Outbox Stuck] sending"
        if not _was_recently_alerted(session, alert_key):
            issue = f"outbox: {stuck_count} messages stuck in 'sending' for >1 hour"
            issues.append(issue)
            _send_alert(
                session,
                recipient,
                alert_key,
                f"{stuck_count} outbound messages have been stuck in 'sending' status "
                f"for over 1 hour.\n\n"
                f"These messages were never delivered and need manual cleanup.\n\n"
                f"Fix: use the CRM admin/maintenance workflow to mark stale sending "
                f"outbox records as failed and add an explicit error reason.",
            )

    return issues


@celery_app.task(name="app.tasks.webhook_health.check_webhook_health")
def check_webhook_health() -> dict:
    """Periodic health check for all webhook/messaging channels.

    Checks:
    1. Channel silence — no inbound messages beyond threshold
    2. Dead letter accumulation — failed messages piling up
    3. Token expiry — OAuth/access tokens expired or expiring
    4. Outbox stuck — outbound messages stuck in sending

    Returns:
        Dict with check results and any issues found.
    """
    start = time.monotonic()
    status = "success"
    session = SessionLocal()
    logger.info("WEBHOOK_HEALTH_CHECK_START")
    all_issues = []

    try:
        recipient = _get_alert_recipient(session)
        if not recipient:
            logger.warning("webhook_health_no_recipient — set 'webhook_health_alert_email' in domain_settings")
            return {"status": "skipped", "reason": "no alert recipient configured"}

        # Run all checks
        all_issues.extend(_check_channel_silence(session, recipient))
        all_issues.extend(_check_dead_letters(session, recipient))
        all_issues.extend(_check_token_expiry(session, recipient))
        all_issues.extend(_check_outbox_stuck(session, recipient))

        session.commit()

        logger.info(
            "WEBHOOK_HEALTH_CHECK_COMPLETE issues=%d details=%s",
            len(all_issues),
            "; ".join(all_issues) if all_issues else "all_healthy",
        )

        return {
            "status": "healthy" if not all_issues else "unhealthy",
            "issues_found": len(all_issues),
            "issues": all_issues,
        }

    except Exception:
        status = "error"
        session.rollback()
        logger.exception("WEBHOOK_HEALTH_CHECK_FAILED")
        raise
    finally:
        session.close()
        observe_job("webhook_health_check", status, time.monotonic() - start)
