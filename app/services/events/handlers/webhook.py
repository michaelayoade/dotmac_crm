"""Webhook handler for the event system.

Creates WebhookDelivery records for matching subscriptions and queues
Celery tasks for HTTP delivery.
"""

import logging
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.webhook import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEventType,
    WebhookSubscription,
)
from app.services.events.types import Event, EventType

logger = logging.getLogger(__name__)

_SELFCARE_CHAT_WEBHOOK_PATHS = frozenset(
    {
        "/api/v1/webhooks/crm/chat",
        "/api/v1/webhooks/crm/field-chat",
    }
)


# Mapping from EventType to WebhookEventType
# This maps our internal event types to the webhook event types stored in DB
# Now that WebhookEventType has been expanded, most events have direct mappings
EVENT_TYPE_TO_WEBHOOK = {
    # Subscriber events
    EventType.subscriber_created: WebhookEventType.subscriber_created,
    EventType.subscriber_updated: WebhookEventType.subscriber_updated,
    EventType.subscriber_suspended: WebhookEventType.subscriber_suspended,
    EventType.subscriber_reactivated: WebhookEventType.subscriber_reactivated,
    # Subscription events
    EventType.subscription_created: WebhookEventType.subscription_created,
    EventType.subscription_activated: WebhookEventType.subscription_activated,
    EventType.subscription_suspended: WebhookEventType.subscription_suspended,
    EventType.subscription_resumed: WebhookEventType.subscription_resumed,
    EventType.subscription_canceled: WebhookEventType.subscription_canceled,
    EventType.subscription_upgraded: WebhookEventType.subscription_upgraded,
    EventType.subscription_downgraded: WebhookEventType.subscription_downgraded,
    EventType.subscription_expiring: WebhookEventType.subscription_expiring,
    # Invoice events
    EventType.invoice_created: WebhookEventType.invoice_created,
    EventType.invoice_sent: WebhookEventType.invoice_sent,
    EventType.invoice_paid: WebhookEventType.invoice_paid,
    EventType.invoice_overdue: WebhookEventType.invoice_overdue,
    # Payment events
    EventType.payment_received: WebhookEventType.payment_received,
    EventType.payment_failed: WebhookEventType.payment_failed,
    EventType.payment_refunded: WebhookEventType.payment_refunded,
    # Usage events
    EventType.usage_recorded: WebhookEventType.usage_recorded,
    EventType.usage_warning: WebhookEventType.usage_warning,
    EventType.usage_exhausted: WebhookEventType.usage_exhausted,
    EventType.usage_topped_up: WebhookEventType.usage_topped_up,
    # Provisioning events
    EventType.provisioning_started: WebhookEventType.provisioning_started,
    EventType.provisioning_completed: WebhookEventType.provisioning_completed,
    EventType.provisioning_failed: WebhookEventType.provisioning_failed,
    # Network events
    EventType.device_offline: WebhookEventType.device_offline,
    EventType.device_online: WebhookEventType.device_online,
    EventType.session_started: WebhookEventType.session_started,
    EventType.session_ended: WebhookEventType.session_ended,
    EventType.network_alert: WebhookEventType.network_alert,
    # Ticket events
    EventType.ticket_created: WebhookEventType.ticket_created,
    EventType.ticket_updated: WebhookEventType.ticket_updated,
    EventType.ticket_escalated: WebhookEventType.ticket_escalated,
    EventType.ticket_resolved: WebhookEventType.ticket_resolved,
    EventType.ticket_comment_created: WebhookEventType.ticket_comment_created,
    # Work-order (field-visit) lifecycle events
    EventType.work_order_dispatched: WebhookEventType.work_order_dispatched,
    EventType.work_order_completed: WebhookEventType.work_order_completed,
    EventType.work_order_canceled: WebhookEventType.work_order_canceled,
    # CRM message events
    EventType.message_outbound: WebhookEventType.message_outbound,
    # Custom
    EventType.custom: WebhookEventType.custom,
}


def _is_redundant_selfcare_chat_subscription(
    subscription: WebhookSubscription, webhook_event_type: WebhookEventType
) -> bool:
    """Skip legacy generic chat subscriptions that duplicate selfcare.notify_*.

    Agent replies already call ``selfcare.notify_chat_message`` /
    ``notify_field_chat_message`` directly. Registering the same sub chat wakeup
    URL as a generic ``message_outbound`` webhook signs a different event
    envelope and fails sub's dedicated receiver authentication. Keep the generic
    webhook bus for real third-party endpoints, but never deliver this internal
    wakeup path through it.
    """
    if webhook_event_type != WebhookEventType.message_outbound:
        return False
    endpoint = subscription.endpoint
    if not endpoint or not endpoint.url:
        return False
    return urlparse(endpoint.url).path.rstrip("/") in _SELFCARE_CHAT_WEBHOOK_PATHS


class WebhookHandler:
    """Handler that creates webhook deliveries for subscribed endpoints."""

    def handle(self, db: Session, event: Event) -> None:
        """Process an event by creating webhook deliveries.

        Finds all active webhook subscriptions for the event type and
        creates a WebhookDelivery record for each. Then queues a Celery
        task to perform the HTTP delivery.

        Args:
            db: Database session
            event: The event to process
        """
        # Map to webhook event type
        webhook_event_type = EVENT_TYPE_TO_WEBHOOK.get(event.event_type)
        if webhook_event_type is None:
            logger.debug(f"No webhook event type mapping for {event.event_type.value}")
            return

        # Find active subscriptions for this event type
        subscriptions = (
            db.query(WebhookSubscription)
            .filter(WebhookSubscription.event_type == webhook_event_type)
            .filter(WebhookSubscription.is_active.is_(True))
            .all()
        )

        if not subscriptions:
            logger.debug(f"No webhook subscriptions for event type {webhook_event_type.value}")
            return

        # Create delivery records
        delivery_ids = []
        for subscription in subscriptions:
            # Verify endpoint is active
            if not subscription.endpoint or not subscription.endpoint.is_active:
                logger.debug(f"Skipping inactive endpoint for subscription {subscription.id}")
                continue
            if _is_redundant_selfcare_chat_subscription(subscription, webhook_event_type):
                logger.info(
                    "Skipping redundant selfcare chat generic webhook subscription %s",
                    subscription.id,
                )
                continue

            delivery = WebhookDelivery(
                subscription_id=subscription.id,
                endpoint_id=subscription.endpoint_id,
                event_type=webhook_event_type,
                status=WebhookDeliveryStatus.pending,
                payload=event.to_dict(),
            )
            db.add(delivery)
            db.flush()  # Get the ID
            delivery_ids.append(str(delivery.id))

        if not delivery_ids:
            return

        # Queue Celery tasks only AFTER the surrounding transaction commits.
        #
        # The delivery rows above are flushed (to obtain their ids) but not yet
        # committed — the emitting service commits later. If we enqueued here,
        # an idle worker could run deliver_webhook (which opens its own session)
        # before the row is visible, see "WebhookDelivery not found", and return
        # early, leaving the row stuck at pending/0-attempts forever. Registering
        # an after_commit hook guarantees the worker can always load the row.
        self._enqueue_after_commit(db, delivery_ids, event)

    @staticmethod
    def _enqueue_after_commit(db: Session, delivery_ids: list[str], event: Event) -> None:
        """Enqueue deliver_webhook tasks once the session commits the rows."""
        from sqlalchemy import event as sa_event

        def _enqueue(session: Session) -> None:
            try:
                from app.tasks.webhooks import deliver_webhook

                for delivery_id in delivery_ids:
                    deliver_webhook.delay(delivery_id)
                logger.info(f"Queued {len(delivery_ids)} webhook deliveries for event {event.event_type.value}")
            except Exception as exc:
                logger.error(f"Failed to queue webhook delivery tasks: {exc}")

        sa_event.listen(db, "after_commit", _enqueue, once=True)
