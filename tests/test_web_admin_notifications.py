from types import SimpleNamespace
from uuid import uuid4

from starlette.requests import Request

from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import Person
from app.services.web_admin_notifications import _extract_target_url, notifications_menu


def _build_request(*, person_id, email: str) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/admin/notifications", "headers": []})
    request.state.user = SimpleNamespace(
        id=uuid4(),
        person_id=person_id,
        email=email,
        first_name="Notify",
        last_name="Tester",
    )
    request.state.auth = {}
    return request


def test_extract_target_url_handles_html_anchor_body():
    body = '<p>You were mentioned.</p><p>Open: <a href="/admin/support/tickets/TKT-1001">/admin/support/tickets/TKT-1001</a></p>'

    assert _extract_target_url(body) == "/admin/support/tickets/TKT-1001"


def test_notifications_menu_only_shows_push_notifications(db_session):
    person = Person(first_name="Push", last_name="Only", email="push-only@example.com")
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)

    db_session.add(
        Notification(
            channel=NotificationChannel.push,
            recipient=person.email,
            subject="New Ticket Assignment: Fiber Cut",
            body="You have been assigned.\nOpen: /admin/support/tickets/TKT-1001",
            status=NotificationStatus.delivered,
        )
    )
    db_session.add(
        Notification(
            channel=NotificationChannel.email,
            recipient=person.email,
            subject="New Ticket Assignment: Fiber Cut",
            body='<p>Open: <a href="/admin/support/tickets/TKT-1001">/admin/support/tickets/TKT-1001</a></p>',
            status=NotificationStatus.queued,
        )
    )
    db_session.commit()

    response = notifications_menu(_build_request(person_id=person.id, email=person.email), db_session)
    items = response.context["notification_items"]

    assert len(items) == 1
    assert items[0]["notification"].channel == NotificationChannel.push
    assert items[0]["target_url"] == "/admin/support/tickets/TKT-1001"
