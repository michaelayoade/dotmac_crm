import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models.notification import Notification
from app.models.person import ChannelType, Person, PersonChannel
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.subscriber_notification import SubscriberNotificationLog
from app.models.tickets import Ticket, TicketPriority, TicketStatus
from app.services import subscriber_notifications as subscriber_notifications_service
from app.web.admin import reports as reports_web


def _request(method: str, path: str, query_string: bytes = b"") -> Request:
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": query_string,
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    request.state.user = SimpleNamespace(
        id=uuid4(),
        person_id=uuid4(),
        email="agent@example.com",
        first_name="Ava",
        last_name="Agent",
    )
    request.state.auth = {"roles": ["admin"], "scopes": ["reports"]}
    return request


def _subscriber(db_session, *, timezone: str = "Africa/Lagos") -> Subscriber:
    person = Person(
        first_name="Taylor",
        last_name="Subscriber",
        display_name="Taylor Subscriber",
        email=f"taylor-{uuid4().hex}@example.com",
        phone="+2348012345678",
        timezone=timezone,
    )
    db_session.add(person)
    db_session.flush()
    db_session.add(
        PersonChannel(
            person_id=person.id,
            channel_type=ChannelType.sms,
            address="+2348012345678",
            is_primary=True,
        )
    )
    subscriber = Subscriber(
        person_id=person.id,
        subscriber_number=f"SUB-{uuid4().hex[:8]}",
        status=SubscriberStatus.active,
        is_active=True,
        service_region="Abuja",
    )
    db_session.add(subscriber)
    db_session.commit()
    db_session.refresh(subscriber)
    return subscriber


def test_enrich_notification_rows_uses_escalated_template_for_urgent_ticket(db_session):
    subscriber = _subscriber(db_session)
    ticket = Ticket(
        subscriber_id=subscriber.id,
        customer_person_id=subscriber.person_id,
        title="Backhaul outage",
        status=TicketStatus.open,
        priority=TicketPriority.urgent,
        is_active=True,
    )
    db_session.add(ticket)
    db_session.commit()

    rows = [
        {
            "subscriber_id": str(subscriber.id),
            "name": "Taylor Subscriber",
            "email": "ignored@example.com",
            "phone": "+2348012345678",
            "timezone": "Africa/Lagos",
            "last_seen_at_iso": "2026-04-27T09:00:00+00:00",
            "last_activity": "router came back online",
        }
    ]

    enriched = subscriber_notifications_service.enrich_notification_rows(rows, db_session)

    assert enriched[0]["notification_template_key"] == "escalated_formal"
    assert "request a callback" in enriched[0]["notification_sms_body"].lower()
    assert enriched[0]["notification_email_subject"] == "Escalated support follow-up"


def test_save_template_bundle_persists_custom_template(db_session):
    saved = subscriber_notifications_service.save_template_bundle(
        db_session,
        template_key="issue_reference",
        email_subject="Open issue follow-up",
        email_body="Hello {name}, your issue is still being handled.",
        sms_body="Hi {name}, your issue is still being handled.",
    )

    assert saved["email_subject"] == "Open issue follow-up"

    subscriber = _subscriber(db_session)
    prepared = subscriber_notifications_service.prepare_subscriber_notification(db_session, subscriber.id)
    assert prepared.template.email_subject == "Checking in on your connection"

    ticket = Ticket(
        subscriber_id=subscriber.id,
        customer_person_id=subscriber.person_id,
        title="Open issue",
        status=TicketStatus.open,
        is_active=True,
    )
    db_session.add(ticket)
    db_session.commit()

    prepared_with_ticket = subscriber_notifications_service.prepare_subscriber_notification(db_session, subscriber.id)
    assert prepared_with_ticket.template.email_subject == "Open issue follow-up"


def test_effective_send_at_uses_next_local_window_when_immediate_send_is_after_hours():
    send_at, display_local = subscriber_notifications_service._effective_send_at(
        "Africa/Lagos",
        None,
        now_utc=datetime(2026, 4, 27, 18, 30, tzinfo=UTC),
    )

    assert send_at == datetime(2026, 4, 28, 8, 0, tzinfo=UTC)
    assert display_local == "2026-04-28T09:00"


def test_queue_subscriber_notification_creates_notifications_logs_and_blocks_duplicates(db_session):
    subscriber = _subscriber(db_session)

    first_logs = subscriber_notifications_service.queue_subscriber_notification(
        db_session,
        subscriber_id=subscriber.id,
        channel_value="both",
        email_subject="Service check-in",
        email_body=(
            "Hi Taylor, we noticed activity on your account. "
            "Your connection looks stable from our side. "
            "If you need help, contact support@example.com. "
            "Thank you for your time."
        ),
        sms_body="Hi Taylor, your connection looks stable. Need help? support@example.com",
        scheduled_local_text="2026-04-29T10:30",
        sent_by_user_id=uuid4(),
        sent_by_person_id=uuid4(),
    )

    assert len(first_logs) == 2
    assert db_session.query(Notification).count() == 0
    assert db_session.query(SubscriberNotificationLog).count() == 2

    with pytest.raises(HTTPException) as excinfo:
        subscriber_notifications_service.queue_subscriber_notification(
            db_session,
            subscriber_id=subscriber.id,
            channel_value="email",
            email_subject="Second try",
            email_body=(
                "Hi Taylor, we noticed activity on your account. "
                "Your connection looks stable from our side. "
                "If you need help, contact support@example.com. "
                "Thank you for your time."
            ),
            sms_body="",
            scheduled_local_text="2026-04-29T11:00",
            sent_by_user_id=uuid4(),
            sent_by_person_id=uuid4(),
        )

    assert excinfo.value.status_code == 409


def test_subscriber_online_last_24h_page_renders_notification_action(monkeypatch):
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {"open_tickets": 0, "dispatch_jobs": 0})

    from app.services import subscriber_notifications as subscriber_notifications_module
    from app.services import subscriber_reports as subscriber_reports_service

    monkeypatch.setattr(subscriber_reports_service, "overview_filter_options", lambda _db: {"regions": ["Abuja"]})
    monkeypatch.setattr(
        subscriber_reports_service, "overview_filtered_subscriber_ids", lambda _db, status=None, region=None: None
    )
    monkeypatch.setattr(
        subscriber_reports_service,
        "online_customers_last_24h_rows",
        lambda _db, subscriber_ids=None, search=None, ticket_status=None, notification_state=None, limit=None: [
            {
                "subscriber_id": str(uuid4()),
                "subscriber_number": "SUB-1001",
                "name": "Taylor Subscriber",
                "status": "active",
                "region": "Abuja",
                "last_seen_at": "Apr 27, 2026 09:00 AM",
                "ticket_id": "",
                "ticket_status": "",
                "email": "taylor@example.com",
                "phone": "+2348012345678",
                "timezone": "Africa/Lagos",
            },
            {
                "subscriber_id": str(uuid4()),
                "subscriber_number": "SUB-1002",
                "name": "Jordan Open",
                "status": "active",
                "region": "Abuja",
                "last_seen_at": "Apr 27, 2026 10:00 AM",
                "ticket_id": str(uuid4()),
                "ticket_status": "open",
                "email": "jordan@example.com",
                "phone": "+2348012345679",
                "timezone": "Africa/Lagos",
            },
            {
                "subscriber_id": str(uuid4()),
                "subscriber_number": "SUB-1003",
                "name": "Casey Closed",
                "status": "active",
                "region": "Abuja",
                "last_seen_at": "Apr 27, 2026 11:00 AM",
                "ticket_id": str(uuid4()),
                "ticket_status": "closed",
                "email": "casey@example.com",
                "phone": "+2348012345680",
                "timezone": "Africa/Lagos",
            },
        ],
    )
    monkeypatch.setattr(
        subscriber_notifications_module,
        "enrich_notification_rows",
        lambda rows, _db: [
            {
                **row,
                "notification_timezone": "Africa/Lagos",
                "notification_template_key": "friendly_check_in",
                "notification_email_subject": "Checking in on your connection",
                "notification_email_body": "Hi Taylor, we noticed activity on your account. If you need help, contact support@example.com.",
                "notification_sms_body": "Hi Taylor, we saw activity on your account. Need help? support@example.com",
                "notification_tokens": "{name}, {last_seen}, {support_email}, {last_activity}",
                "latest_notification_channel": "sms" if index == 0 else "",
                "latest_notification_status": "testing_hold" if index == 0 else "",
                "latest_notification_scheduled_for": "Apr 27, 2026 10:30 AM" if index == 0 else "",
                "latest_notification_message_body": "Hi Taylor, we saw activity on your account. Need help? support@example.com"
                if index == 0
                else "",
            }
            for index, row in enumerate(rows)
        ],
    )

    response = reports_web.subscriber_online_last_24h(
        request=_request("GET", "/admin/reports/subscribers/online-last-24h"),
        db=None,
        status=None,
        region=None,
        search=None,
        ticket_status="all",
        notification_state="all",
    )

    body = response.body.decode()
    assert response.status_code == 200
    assert "Send customer follow-up" in body
    assert "Queue Notification" in body
    assert "Test mode is active" in body
    assert "Testing Hold" in body
    assert "SMS" in body
    assert "Scheduled: Apr 27, 2026 10:30 AM" in body
    assert "Queued Notification" in body
    assert "Total" in body
    assert "No Ticket" in body
    assert "Open" in body
    assert "Closed" in body
    assert "Notification State" in body
    assert "Priority Score" in body
    assert "Message Templates" in body
    assert "Save Template" in body
    assert "Activity Log" in body
    assert "Waiting On Customer" in body
    assert "Site Under Construction" in body
    assert "With Ticket" not in body
    assert "Ticket Statuses" not in body
    assert "Open ticket" not in body
    assert "data-notify-button" in body


def test_enrich_notification_rows_includes_latest_queued_notification_summary(db_session):
    subscriber = _subscriber(db_session)
    subscriber_notifications_service.queue_subscriber_notification(
        db_session,
        subscriber_id=subscriber.id,
        channel_value="sms",
        email_subject=None,
        email_body=None,
        sms_body="Hi Taylor, queued reminder.",
        scheduled_local_text="2026-04-29T10:30",
        sent_by_user_id=uuid4(),
        sent_by_person_id=uuid4(),
    )[0]

    rows = [
        {
            "subscriber_id": str(subscriber.id),
            "name": "Taylor Subscriber",
            "email": subscriber.person.email,
            "phone": subscriber.person.phone,
            "timezone": "Africa/Lagos",
            "last_seen_at_iso": "2026-04-27T09:00:00+00:00",
            "last_activity": "router came back online",
        }
    ]

    enriched = subscriber_notifications_service.enrich_notification_rows(rows, db_session)

    assert enriched[0]["latest_notification_channel"] == "sms"
    assert enriched[0]["latest_notification_status"] == "testing_hold"
    assert "queued reminder" in enriched[0]["latest_notification_message_body"].lower()
    assert enriched[0]["latest_notification_scheduled_for"]


def test_subscriber_online_last_24h_notify_route_queues_notification(db_session):
    subscriber = _subscriber(db_session)

    response = reports_web.subscriber_online_last_24h_notify(
        request=_request("POST", "/admin/reports/subscribers/online-last-24h/notify"),
        subscriber_id=subscriber.id,
        channel="sms",
        email_subject=None,
        email_body=None,
        sms_body="Hi Taylor, we saw activity at 10:30 AM. Need help? support@example.com",
        scheduled_local_at="2026-04-29T10:30",
        next_url="/admin/reports/subscribers/online-last-24h",
        db=db_session,
    )

    assert response.status_code == 303
    assert db_session.query(Notification).count() == 0
    assert db_session.query(SubscriberNotificationLog).count() == 1


def test_subscriber_online_last_24h_passes_notification_state_filter(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {})
    from app.services import subscriber_notifications as subscriber_notifications_module
    from app.services import subscriber_reports as subscriber_reports_service

    monkeypatch.setattr(subscriber_reports_service, "overview_filter_options", lambda _db: {"regions": []})
    monkeypatch.setattr(
        subscriber_reports_service, "overview_filtered_subscriber_ids", lambda _db, status=None, region=None: None
    )

    def _rows(_db, subscriber_ids=None, search=None, ticket_status=None, notification_state=None, limit=None):
        captured["notification_state"] = notification_state
        return []

    monkeypatch.setattr(subscriber_reports_service, "online_customers_last_24h_rows", _rows)
    monkeypatch.setattr(subscriber_notifications_module, "enrich_notification_rows", lambda rows, _db: rows)

    response = reports_web.subscriber_online_last_24h(
        request=_request("GET", "/admin/reports/subscribers/online-last-24h"),
        db=None,
        status=None,
        region=None,
        search=None,
        ticket_status="all",
        notification_state="notified",
    )

    assert response.status_code == 200
    assert captured["notification_state"] == "notified"


def test_subscriber_online_last_24h_notify_context_route_returns_templates_and_activity(db_session):
    subscriber = _subscriber(db_session)
    ticket = Ticket(
        subscriber_id=subscriber.id,
        customer_person_id=subscriber.person_id,
        title="Pending outage review",
        status=TicketStatus.pending,
        is_active=True,
    )
    db_session.add(ticket)
    db_session.commit()

    response = reports_web.subscriber_online_last_24h_notify_context(
        subscriber_id=subscriber.id,
        last_seen_at="Apr 27, 2026 09:00 AM",
        last_activity="router came back online",
        db=db_session,
    )

    assert response.status_code == 200
    payload = json.loads(response.body.decode())
    assert payload["template_key"] == "issue_reference"
    assert "issue_reference" in payload["templates"]
    assert "priority" in payload
    assert "activity_log" in payload


def test_subscriber_online_last_24h_save_template_route_persists_bundle(db_session):
    response = reports_web.subscriber_online_last_24h_save_template(
        template_key="friendly_check_in",
        email_subject="New subject",
        email_body="Hi {name}, custom email body.",
        sms_body="Hi {name}, custom sms body.",
        db=db_session,
    )

    assert response.status_code == 200
    payload = json.loads(response.body.decode())
    assert payload["ok"] is True
    assert payload["template"]["email_subject"] == "New subject"
