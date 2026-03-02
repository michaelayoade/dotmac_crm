from app.models.notification import DeliveryStatus, Notification, NotificationChannel, NotificationStatus
from app.tasks.notifications import _deliver_notification_queue


def test_push_notifications_remain_delivered_when_talk_forward_fails(db_session, person, monkeypatch):
    notification = Notification(
        channel=NotificationChannel.push,
        recipient=person.email,
        subject="Mentioned in ticket: Ticket TKT-1001",
        body="You were mentioned.\nOpen: /admin/support/tickets/TKT-1001",
        status=NotificationStatus.queued,
    )
    db_session.add(notification)
    db_session.commit()
    db_session.refresh(notification)

    monkeypatch.setattr(
        "app.services.nextcloud_talk_notifications.forward_stored_notification",
        lambda db, notification: False,
    )

    delivered = _deliver_notification_queue(db_session, batch_size=10)
    db_session.refresh(notification)

    assert delivered == 1
    assert notification.status == NotificationStatus.delivered
    assert notification.last_error is None
    assert notification.sent_at is not None

    delivery = notification.deliveries[0]
    assert delivery.provider == "nextcloud_talk"
    assert delivery.status == DeliveryStatus.failed
    assert delivery.response_body == "talk_forward_failed"
