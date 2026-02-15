from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import and_, exists, or_

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.campaign import Campaign, CampaignRecipient
from app.models.crm.campaign_smtp import CampaignSmtpConfig
from app.models.notification import (
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationStatus,
)
from app.services import email as email_service
from app.services import nextcloud_talk_notifications as talk_notifications_service
from app.services.branding import get_branding

# Timeout for stuck "sending" notifications (5 minutes)
SENDING_TIMEOUT_MINUTES = 5


def _deliver_notification_queue(db, batch_size: int = 50) -> int:
    now = datetime.now(UTC)
    stuck_threshold = now - timedelta(minutes=SENDING_TIMEOUT_MINUTES)

    # Query both queued notifications and stuck "sending" notifications
    notifications = (
        db.query(Notification)
        .filter(Notification.is_active.is_(True))
        .filter(Notification.channel == NotificationChannel.email)
        .filter(
            or_(
                # Queued notifications ready to send
                Notification.status == NotificationStatus.queued,
                # Stuck "sending" notifications (likely crashed during send)
                # Use updated_at to detect stuck notifications
                ((Notification.status == NotificationStatus.sending) & (Notification.updated_at < stuck_threshold)),
            )
        )
        .filter((Notification.send_at.is_(None)) | (Notification.send_at <= now))
        .order_by(Notification.created_at.asc())
        .limit(batch_size)
        .all()
    )
    delivered = 0
    for notification in notifications:
        # Update status before sending - updated_at auto-updates
        notification.status = NotificationStatus.sending
        db.commit()

        subject = notification.subject or "Notification"
        body = notification.body or ""
        try:
            if not notification.smtp_config_id:
                campaign_smtp_id = (
                    db.query(Campaign.campaign_smtp_config_id)
                    .join(
                        CampaignRecipient,
                        CampaignRecipient.campaign_id == Campaign.id,
                    )
                    .filter(CampaignRecipient.notification_id == notification.id)
                    .scalar()
                )
                if campaign_smtp_id:
                    notification.smtp_config_id = campaign_smtp_id
                    db.commit()

            if notification.smtp_config_id:
                smtp_profile = db.get(CampaignSmtpConfig, notification.smtp_config_id)
                if not smtp_profile or not smtp_profile.is_active:
                    raise ValueError("SMTP profile not found or inactive")
                smtp_config = {
                    "host": smtp_profile.host,
                    "port": smtp_profile.port,
                    "username": smtp_profile.username,
                    "password": smtp_profile.password,
                    "use_tls": smtp_profile.use_tls,
                    "use_ssl": smtp_profile.use_ssl,
                    "from_name": notification.from_name or get_branding(db)["company_name"],
                    "from_email": notification.from_email or "noreply@example.com",
                    "from_addr": notification.from_email or "noreply@example.com",
                }
                success, _ = email_service.send_email_with_config(
                    smtp_config,
                    notification.recipient,
                    subject,
                    body,
                    body_text=None,
                    reply_to=notification.reply_to,
                )
            else:
                success, _ = email_service.send_email(
                    db=db,
                    to_email=notification.recipient,
                    subject=subject,
                    body_html=body,
                    body_text=None,
                    track=False,
                    from_name=notification.from_name,
                    from_email=notification.from_email,
                    reply_to=notification.reply_to,
                )
        except Exception as exc:
            success = False
            notification.last_error = str(exc)
        if success:
            notification.status = NotificationStatus.delivered
            notification.sent_at = datetime.now(UTC)
            notification.last_error = None
            delivered += 1
        else:
            notification.status = NotificationStatus.failed
            if not notification.last_error:
                notification.last_error = "send_email_failed"
        db.commit()

    whatsapp_notifications = (
        db.query(Notification)
        .filter(Notification.is_active.is_(True))
        .filter(Notification.channel == NotificationChannel.whatsapp)
        .filter(
            or_(
                Notification.status == NotificationStatus.queued,
                ((Notification.status == NotificationStatus.sending) & (Notification.updated_at < stuck_threshold)),
            )
        )
        .filter((Notification.send_at.is_(None)) | (Notification.send_at <= now))
        .order_by(Notification.created_at.asc())
        .limit(batch_size)
        .all()
    )
    for notification in whatsapp_notifications:
        notification.status = NotificationStatus.sending
        db.commit()

        try:
            if not notification.connector_config_id:
                raise ValueError("WhatsApp connector config is required")

            config = db.get(ConnectorConfig, notification.connector_config_id)
            if not config or not config.is_active:
                raise ValueError("WhatsApp connector not found or inactive")
            if config.connector_type != ConnectorType.whatsapp:
                raise ValueError("Connector is not a WhatsApp connector")

            auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
            metadata = config.metadata_ if isinstance(config.metadata_, dict) else {}
            token = auth_config.get("token") or auth_config.get("access_token")
            if not token:
                raise ValueError("WhatsApp access token missing")
            phone_number_id = metadata.get("phone_number_id") or auth_config.get("phone_number_id")
            if not phone_number_id:
                raise ValueError("WhatsApp phone_number_id missing")

            # Check if this notification was created from a campaign with a WhatsApp template
            wa_template_name = notification.subject  # campaign sets template name as subject
            wa_template_lang = None
            wa_template_components = None
            if wa_template_name:
                # Look up the campaign for template language and components
                campaign_row = (
                    db.query(Campaign.whatsapp_template_language, Campaign.whatsapp_template_components)
                    .join(CampaignRecipient, CampaignRecipient.campaign_id == Campaign.id)
                    .filter(CampaignRecipient.notification_id == notification.id)
                    .first()
                )
                if campaign_row:
                    wa_template_lang = campaign_row[0]
                    wa_template_components = campaign_row[1]

            if wa_template_name and wa_template_lang:
                payload_data: dict = {
                    "messaging_product": "whatsapp",
                    "to": notification.recipient,
                    "type": "template",
                    "template": {
                        "name": wa_template_name,
                        "language": {"code": wa_template_lang},
                    },
                }
                if wa_template_components and isinstance(wa_template_components, dict):
                    payload_data["template"]["components"] = wa_template_components.get("components", [])
                elif wa_template_components and isinstance(wa_template_components, list):
                    payload_data["template"]["components"] = wa_template_components
            else:
                payload_data = {
                    "messaging_product": "whatsapp",
                    "to": notification.recipient,
                    "type": "text",
                    "text": {"body": notification.body or ""},
                }
            headers = {"Authorization": f"Bearer {token}"}
            if isinstance(config.headers, dict):
                headers.update(config.headers)
            base_url = config.base_url or "https://graph.facebook.com/v19.0"
            response = httpx.post(
                f"{base_url.rstrip('/')}/{phone_number_id}/messages",
                json=payload_data,
                headers=headers,
                timeout=config.timeout_sec or 20,
            )
            response.raise_for_status()
            notification.status = NotificationStatus.delivered
            notification.sent_at = datetime.now(UTC)
            notification.last_error = None
            delivered += 1
        except Exception as exc:
            notification.status = NotificationStatus.failed
            notification.last_error = str(exc)
        db.commit()

    push_notifications = (
        db.query(Notification)
        .filter(Notification.is_active.is_(True))
        .filter(Notification.channel == NotificationChannel.push)
        .filter((Notification.send_at.is_(None)) | (Notification.send_at <= now))
        .filter(
            ~exists().where(
                and_(
                    NotificationDelivery.notification_id == Notification.id,
                    NotificationDelivery.provider == "nextcloud_talk",
                )
            )
        )
        .order_by(Notification.created_at.asc())
        .limit(batch_size)
        .all()
    )
    for notification in push_notifications:
        success = talk_notifications_service.forward_stored_notification(db, notification=notification)
        delivery = NotificationDelivery(
            notification_id=notification.id,
            provider="nextcloud_talk",
            status=DeliveryStatus.delivered if success else DeliveryStatus.failed,
            response_code="200" if success else "500",
            response_body="forwarded" if success else "talk_forward_failed",
        )
        db.add(delivery)
        if success:
            notification.status = NotificationStatus.delivered
            notification.sent_at = datetime.now(UTC)
            notification.last_error = None
            delivered += 1
        else:
            notification.status = NotificationStatus.failed
            if not notification.last_error:
                notification.last_error = "talk_forward_failed"
        db.commit()

    return delivered


@celery_app.task(name="app.tasks.notifications.deliver_notification_queue")
def deliver_notification_queue():
    session = SessionLocal()
    try:
        _deliver_notification_queue(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
