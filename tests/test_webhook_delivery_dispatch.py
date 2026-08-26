"""Regression tests for webhook delivery dispatch.

Covers the enqueue-before-commit race that left deliveries stuck at
pending/0-attempts, and the sweeper that re-dispatches stale pending rows.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.subscriber import Subscriber
from app.models.tickets import TicketPriority, TicketStatus
from app.models.webhook import (
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookEndpoint,
    WebhookEventType,
    WebhookSubscription,
)
from app.schemas.tickets import TicketCommentCreate, TicketCreate, TicketUpdate
from app.services import tickets as tickets_service
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


def _add_subscription(db_session, event_type: WebhookEventType) -> WebhookEndpoint:
    endpoint = WebhookEndpoint(
        name=f"Endpoint {event_type.value}",
        url=f"https://example.test/{event_type.name}",
        secret="s3cr3t",
        is_active=True,
    )
    db_session.add(endpoint)
    db_session.flush()
    db_session.add(
        WebhookSubscription(
            endpoint_id=endpoint.id,
            event_type=event_type,
            is_active=True,
        )
    )
    db_session.commit()
    return endpoint


def _selfcare_subscriber(db_session, person, external_id: str = "selfcare-sub-1") -> Subscriber:
    subscriber = Subscriber(
        person_id=person.id,
        external_system="selfcare",
        external_id=external_id,
        subscriber_number=f"SUB-{external_id}",
        is_active=True,
    )
    db_session.add(subscriber)
    db_session.commit()
    db_session.refresh(subscriber)
    return subscriber


def _ticket_for_selfcare_subscriber(db_session, person):
    subscriber = _selfcare_subscriber(db_session, person)
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            subscriber_id=subscriber.id,
            title="Router offline",
            description="Customer router is offline",
            priority=TicketPriority.normal,
            tags=["connectivity"],
        ),
    )
    return subscriber, ticket


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


def test_ticket_webhook_event_type_mapping_includes_update_and_comment_created(db_session):
    cases = [
        (EventType.ticket_created, WebhookEventType.ticket_created),
        (EventType.ticket_updated, WebhookEventType.ticket_updated),
        (EventType.ticket_escalated, WebhookEventType.ticket_escalated),
        (EventType.ticket_resolved, WebhookEventType.ticket_resolved),
        (EventType.ticket_comment_created, WebhookEventType.ticket_comment_created),
    ]
    for event_type, webhook_event_type in cases:
        _add_subscription(db_session, webhook_event_type)
        with patch("app.tasks.webhooks.deliver_webhook.delay"):
            WebhookHandler().handle(db_session, Event(event_type=event_type, payload={"ticket_id": "t-1"}))
            db_session.commit()

        delivery = (
            db_session.query(WebhookDelivery)
            .filter(WebhookDelivery.event_type == webhook_event_type)
            .order_by(WebhookDelivery.created_at.desc())
            .first()
        )
        assert delivery is not None
        assert delivery.payload["event_type"] == event_type.value


def test_ticket_update_creates_durable_webhook_with_full_selfcare_payload(db_session, person):
    subscriber, ticket = _ticket_for_selfcare_subscriber(db_session, person)
    _add_subscription(db_session, WebhookEventType.ticket_updated)

    with patch("app.tasks.webhooks.deliver_webhook.delay") as mock_delay:
        tickets_service.tickets.update(
            db_session,
            str(ticket.id),
            TicketUpdate(title="Router offline - updated", priority=TicketPriority.high),
        )

    delivery = (
        db_session.query(WebhookDelivery).filter(WebhookDelivery.event_type == WebhookEventType.ticket_updated).one()
    )
    payload = delivery.payload["payload"]
    assert payload["subscriber_id"] == subscriber.external_id
    assert payload["ticket"]["id"] == str(ticket.id)
    assert payload["ticket"]["subscriber_id"] == str(subscriber.id)
    assert payload["ticket"]["title"] == "Router offline - updated"
    assert payload["ticket"]["priority"] == TicketPriority.high.value
    assert payload["ticket"]["tags"] == ["connectivity"]
    assert payload["changed_fields"] == ["title", "priority"]
    assert delivery.payload["context"]["subscriber_id"] == str(subscriber.id)
    mock_delay.assert_called_once_with(str(delivery.id))


def test_ticket_resolved_also_creates_ticket_updated_delivery(db_session, person):
    subscriber, ticket = _ticket_for_selfcare_subscriber(db_session, person)
    _add_subscription(db_session, WebhookEventType.ticket_resolved)
    _add_subscription(db_session, WebhookEventType.ticket_updated)

    with patch("app.tasks.webhooks.deliver_webhook.delay") as mock_delay:
        tickets_service.tickets.update(
            db_session,
            str(ticket.id),
            TicketUpdate(status=TicketStatus.closed, closed_at=ticket.created_at),
        )

    deliveries = db_session.query(WebhookDelivery).all()
    event_types = {delivery.event_type for delivery in deliveries}
    assert WebhookEventType.ticket_resolved in event_types
    assert WebhookEventType.ticket_updated in event_types
    updated = next(delivery for delivery in deliveries if delivery.event_type == WebhookEventType.ticket_updated)
    assert updated.payload["payload"]["subscriber_id"] == subscriber.external_id
    assert updated.payload["payload"]["ticket"]["status"] == TicketStatus.closed.value
    assert mock_delay.call_count == 2


def test_ticket_escalated_also_creates_ticket_updated_delivery(db_session, person):
    subscriber, ticket = _ticket_for_selfcare_subscriber(db_session, person)
    _add_subscription(db_session, WebhookEventType.ticket_escalated)
    _add_subscription(db_session, WebhookEventType.ticket_updated)

    with patch("app.tasks.webhooks.deliver_webhook.delay") as mock_delay:
        tickets_service.tickets.update(
            db_session,
            str(ticket.id),
            TicketUpdate(priority=TicketPriority.urgent),
        )

    deliveries = db_session.query(WebhookDelivery).all()
    event_types = {delivery.event_type for delivery in deliveries}
    assert WebhookEventType.ticket_escalated in event_types
    assert WebhookEventType.ticket_updated in event_types
    updated = next(delivery for delivery in deliveries if delivery.event_type == WebhookEventType.ticket_updated)
    assert updated.payload["payload"]["subscriber_id"] == subscriber.external_id
    assert updated.payload["payload"]["ticket"]["priority"] == TicketPriority.urgent.value
    assert mock_delay.call_count == 2


def test_ticket_comment_create_creates_durable_webhook_with_full_selfcare_payload(db_session, person):
    subscriber, ticket = _ticket_for_selfcare_subscriber(db_session, person)
    _add_subscription(db_session, WebhookEventType.ticket_comment_created)

    with patch("app.tasks.webhooks.deliver_webhook.delay") as mock_delay:
        comment = tickets_service.ticket_comments.create(
            db_session,
            TicketCommentCreate(
                ticket_id=ticket.id,
                author_person_id=person.id,
                body="Customer confirmed issue persists",
                attachments=[{"name": "speedtest.png"}],
            ),
        )

    delivery = (
        db_session.query(WebhookDelivery)
        .filter(WebhookDelivery.event_type == WebhookEventType.ticket_comment_created)
        .one()
    )
    payload = delivery.payload["payload"]
    assert payload["subscriber_id"] == subscriber.external_id
    assert payload["ticket_id"] == str(ticket.id)
    assert payload["ticket"]["id"] == str(ticket.id)
    assert payload["comment"]["id"] == str(comment.id)
    assert payload["comment"]["ticket_id"] == str(ticket.id)
    assert payload["comment"]["author_person_id"] == str(person.id)
    assert payload["comment"]["body"] == "Customer confirmed issue persists"
    assert payload["comment"]["is_internal"] is False
    assert payload["comment"]["attachments"] == [{"name": "speedtest.png"}]
    mock_delay.assert_called_once_with(str(delivery.id))


def test_ticket_update_and_comment_webhooks_skip_without_selfcare_subscriber_mapping(db_session):
    ticket = tickets_service.tickets.create(db_session, TicketCreate(title="Unmapped ticket"))
    _add_subscription(db_session, WebhookEventType.ticket_updated)
    _add_subscription(db_session, WebhookEventType.ticket_comment_created)

    with patch("app.tasks.webhooks.deliver_webhook.delay") as mock_delay:
        tickets_service.tickets.update(
            db_session,
            str(ticket.id),
            TicketUpdate(title="Unmapped ticket updated"),
        )
        tickets_service.ticket_comments.create(
            db_session,
            TicketCommentCreate(ticket_id=ticket.id, body="Unmapped comment"),
        )

    count = (
        db_session.query(WebhookDelivery)
        .filter(
            WebhookDelivery.event_type.in_([WebhookEventType.ticket_updated, WebhookEventType.ticket_comment_created])
        )
        .count()
    )
    assert count == 0
    mock_delay.assert_not_called()


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


def test_outbound_message_skips_redundant_selfcare_chat_webhook(db_session):
    endpoint = WebhookEndpoint(
        name="DotMac Sub - chat push",
        url="https://selfcare.dotmac.io/api/v1/webhooks/crm/chat",
        secret="s3cr3t",
        is_active=True,
    )
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

    event = Event(
        event_type=EventType.message_outbound,
        payload={
            "conversation_id": "conv-1",
            "subscriber_id": "sub-1",
            "preview": "Reply",
        },
    )
    with patch("app.tasks.webhooks.deliver_webhook.delay") as mock_delay:
        WebhookHandler().handle(db_session, event)
        db_session.commit()

    count = (
        db_session.query(WebhookDelivery)
        .filter(WebhookDelivery.event_type == WebhookEventType.message_outbound)
        .count()
    )
    assert count == 0
    mock_delay.assert_not_called()


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
