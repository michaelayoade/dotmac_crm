from types import SimpleNamespace

from app.models.notification import Notification, NotificationChannel
from app.models.person import Person
from app.services.agent_mentions import notify_agent_mentions
from app.services.ticket_mentions import notify_ticket_comment_mentions
from app.services.web_admin_notifications import _supports_view_action


def test_ticket_mention_creates_in_app_notification_with_open_link(db_session, monkeypatch):
    person = Person(first_name="Ticket", last_name="Mention", email="ticket-mention@example.com")
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)

    monkeypatch.setattr("app.websocket.broadcaster.broadcast_agent_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.ticket_mentions.queue_mention_email_notifications", lambda *args, **kwargs: None)

    notify_ticket_comment_mentions(
        db_session,
        ticket_id="2bc2e38e-2a7a-4d37-87ce-e2fbbf7f2f11",
        ticket_number="TKT-1001",
        ticket_title="Fiber cut",
        comment_preview="Please take a look",
        mentioned_agent_ids=[f"person:{person.id}"],
        actor_person_id=None,
    )

    note = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == person.email)
        .filter(Notification.subject.like("Mentioned in ticket:%"))
        .one()
    )
    assert "Open: /admin/support/tickets/TKT-1001" in (note.body or "")


def test_project_mention_creates_in_app_notification_with_open_link(db_session, monkeypatch):
    person = Person(first_name="Project", last_name="Mention", email="project-mention@example.com")
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)

    monkeypatch.setattr("app.websocket.broadcaster.broadcast_agent_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.agent_mentions.queue_mention_email_notifications", lambda *args, **kwargs: None)

    notify_agent_mentions(
        db_session,
        mentioned_agent_ids=[f"person:{person.id}"],
        actor_person_id=None,
        payload={
            "kind": "mention",
            "title": "Mentioned in project",
            "subtitle": "Project PRJ-42 · Backbone Upgrade",
            "preview": "Need your review",
            "target_url": "/admin/projects/PRJ-42",
            "project_id": "7d33058f-6518-474b-9a1c-2c557a55d3b2",
            "project_number": "PRJ-42",
        },
    )

    note = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == person.email)
        .filter(Notification.subject.like("Mentioned in project:%"))
        .one()
    )
    assert "Open: /admin/projects/PRJ-42" in (note.body or "")


def test_mentions_support_view_action():
    assert _supports_view_action(SimpleNamespace(subject="Mentioned in ticket: Ticket TKT-1001"))
    assert _supports_view_action(SimpleNamespace(subject="Mentioned in project: Project PRJ-42"))
    assert _supports_view_action(SimpleNamespace(subject="New Project Assignment: Backbone Upgrade"))
