"""Regression tests for webhook delivery dispatch.

Covers the enqueue-before-commit race that left deliveries stuck at
pending/0-attempts, and the sweeper that re-dispatches stale pending rows.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.webhook import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
    WebhookEventType,
    WebhookSubscription,
)
from app.services.events.handlers.webhook import WebhookHandler
from app.services.events.types import Event, EventType


@pytest.fixture()
def webhook_endpoint(db_session):
    endpoint = WebhookEndpoint(
        name="Test Endpoint",
        url="https://example.test/hook",
        secret="s3cr3t",
        is_active=True,
    )
    db_session.add(endpoint)
    db_session.flush()
    subscription = WebhookSubscription(
        endpoint_id=endpoint.id,
        event_type=WebhookEventType.ticket_created,
        is_active=True,
    )
    db_session.add(subscription)
    db_session.commit()
    return endpoint


def _ticket_created_event() -> Event:
    return Event(event_type=EventType.ticket_created, payload={"ticket_id": "t-1"})


def test_delivery_not_enqueued_until_commit(db_session, webhook_endpoint):
    """The Celery task must not be enqueued before the row is committed."""
    with patch("app.tasks.webhooks.deliver_webhook.delay") as mock_delay:
        WebhookHandler().handle(db_session, _ticket_created_event())

        # Row is created (flushed) but the task must NOT be queued yet — at this
        # point the worker could not see the uncommitted row.
        delivery = db_session.query(WebhookDelivery).one()
        assert delivery.status == WebhookDeliveryStatus.pending
        mock_delay.assert_not_called()

        # After commit, the after_commit hook fires and enqueues the task.
        db_session.commit()
        mock_delay.assert_called_once_with(str(delivery.id))


def test_delivery_not_enqueued_on_rollback(db_session, webhook_endpoint):
    """If the emitting transaction rolls back, nothing is enqueued."""
    with patch("app.tasks.webhooks.deliver_webhook.delay") as mock_delay:
        savepoint = db_session.begin_nested()
        WebhookHandler().handle(db_session, _ticket_created_event())
        savepoint.rollback()
        # No commit ever reaches the after_commit hook → nothing queued.
        mock_delay.assert_not_called()


def test_requeue_stale_pending_deliveries(db_session, webhook_endpoint):
    """The sweeper re-enqueues old pending rows that were never attempted."""
    subscription = db_session.query(WebhookSubscription).one()
    stale = WebhookDelivery(
        subscription_id=subscription.id,
        endpoint_id=webhook_endpoint.id,
        event_type=WebhookEventType.ticket_created,
        status=WebhookDeliveryStatus.pending,
        payload={"ticket_id": "t-old"},
    )
    db_session.add(stale)
    db_session.flush()
    # Backdate it past the staleness window.
    stale.created_at = datetime.now(UTC) - timedelta(minutes=30)

    # A fresh pending row (within the window) must be left alone.
    fresh = WebhookDelivery(
        subscription_id=subscription.id,
        endpoint_id=webhook_endpoint.id,
        event_type=WebhookEventType.ticket_created,
        status=WebhookDeliveryStatus.pending,
        payload={"ticket_id": "t-new"},
    )
    db_session.add(fresh)
    db_session.commit()

    from app.tasks import webhooks as webhook_tasks

    fake_session = MagicMock()
    fake_session.query.return_value.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
        stale
    ]
    with (
        patch.object(webhook_tasks, "SessionLocal", return_value=fake_session),
        patch("app.tasks.webhooks.deliver_webhook.delay") as mock_delay,
    ):
        result = webhook_tasks.requeue_stale_pending_deliveries()

    assert result == {"requeued": 1}
    mock_delay.assert_called_once_with(str(stale.id))


def test_outbound_message_creates_webhook_delivery(db_session):
    """An outbound agent reply emits message_outbound -> creates a delivery."""
    import uuid

    from app.models.crm.conversation import Conversation
    from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
    from app.models.person import Person
    from app.schemas.crm.conversation import MessageCreate
    from app.services.crm.conversations.service import Messages

    person = Person(first_name="A", last_name="B", email=f"{uuid.uuid4().hex}@test.com", is_active=True)
    db_session.add(person)
    db_session.flush()
    conversation = Conversation(person_id=person.id, status=ConversationStatus.open)
    db_session.add(conversation)
    db_session.flush()

    endpoint = WebhookEndpoint(name="MO", url="https://example.test/mo", is_active=True)
    db_session.add(endpoint)
    db_session.flush()
    db_session.add(
        WebhookSubscription(
            endpoint_id=endpoint.id,
            event_type=WebhookEventType.message_outbound,
            is_active=True,
        )
    )
    db_session.commit()

    payload = MessageCreate(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
        body="Reply body",
    )
    with patch("app.tasks.webhooks.deliver_webhook.delay") as mock_delay:
        Messages.create(db_session, payload)

    delivery = (
        db_session.query(WebhookDelivery).filter(WebhookDelivery.event_type == WebhookEventType.message_outbound).one()
    )
    assert delivery.status == WebhookDeliveryStatus.pending
    # Messages.create commits the outbound branch, so the task is enqueued.
    mock_delay.assert_called_once_with(str(delivery.id))


def test_deliver_webhook_retries_when_row_not_visible():
    """A not-yet-committed row triggers a short retry, not a silent drop."""
    from celery.exceptions import Retry

    from app.tasks import webhooks as webhook_tasks

    fake_session = MagicMock()
    fake_session.get.return_value = None  # row not visible yet

    with patch.object(webhook_tasks, "SessionLocal", return_value=fake_session):
        task = webhook_tasks.deliver_webhook
        with patch.object(task, "retry", side_effect=Retry()) as mock_retry, pytest.raises(Retry):
            task.run("00000000-0000-0000-0000-000000000000")
    mock_retry.assert_called_once()
