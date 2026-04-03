from app.models.crm.conversation import Conversation
from app.models.notification import Notification, NotificationChannel
from app.models.person import Person
from app.services.crm.conversations.service import Conversations


def _create_person(db_session, *, email: str, first_name: str = "Test", last_name: str = "User") -> Person:
    person = Person(first_name=first_name, last_name=last_name, email=email)
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


def test_escalate_conversation_to_talk_creates_push_and_forwards(db_session, person, monkeypatch):
    recipient = _create_person(
        db_session,
        email="recipient@example.com",
        first_name="Recipient",
        last_name="Person",
    )

    conversation = Conversation(person_id=person.id, subject="Billing Follow-up")
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)

    captured = {"person_id": None, "payload": None}

    def _fake_forward(db, *, person_id, payload):
        captured["person_id"] = person_id
        captured["payload"] = payload
        return True

    monkeypatch.setattr(
        "app.services.nextcloud_talk_notifications.forward_agent_notification",
        _fake_forward,
    )

    result = Conversations.escalate_to_talk(
        db_session,
        conversation_id=str(conversation.id),
        recipient_person_id=str(recipient.id),
        actor_person_id=str(person.id),
        note="Please take over this thread ASAP.",
        urgency="critical",
    )

    assert result["ok"] is True
    assert result["talk_forwarded"] is True
    assert result["conversation_id"] == str(conversation.id)
    assert result["recipient_person_id"] == str(recipient.id)
    assert "conversation_id=" in str(result["target_url"])

    notification = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == recipient.email)
        .order_by(Notification.created_at.desc())
        .first()
    )
    assert notification is not None
    assert notification.subject and "Conversation Escalation" in notification.subject
    assert notification.body and "Urgency: critical" in notification.body

    assert captured["person_id"] == str(recipient.id)
    assert captured["payload"] is not None
    assert captured["payload"]["kind"] == "conversation_escalation"
    assert captured["payload"]["conversation_id"] == str(conversation.id)
    assert "Billing Follow-up" in str(captured["payload"]["subtitle"])


def test_escalate_conversation_to_talk_returns_false_when_talk_not_forwarded(db_session, person, monkeypatch):
    recipient = _create_person(
        db_session,
        email="recipient2@example.com",
        first_name="Recipient",
        last_name="Two",
    )
    conversation = Conversation(person_id=person.id, subject="Support Case")
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)

    monkeypatch.setattr(
        "app.services.nextcloud_talk_notifications.forward_agent_notification",
        lambda db, *, person_id, payload: False,
    )

    result = Conversations.escalate_to_talk(
        db_session,
        conversation_id=str(conversation.id),
        recipient_person_id=str(recipient.id),
        actor_person_id=str(person.id),
    )

    assert result["ok"] is True
    assert result["talk_forwarded"] is False
