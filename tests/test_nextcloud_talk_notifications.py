from app.models.nextcloud_talk_notification import NextcloudTalkNotificationRoom
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.schemas.settings import DomainSettingUpdate
from app.services import nextcloud_talk_notifications, settings_api
from app.services.nextcloud_talk import NextcloudTalkError


def _configure_talk_notifications(db_session) -> None:
    settings_api.upsert_notification_setting(
        db_session,
        "nextcloud_talk_notifications_enabled",
        DomainSettingUpdate(value_json=True),
    )
    settings_api.upsert_notification_setting(
        db_session,
        "nextcloud_talk_notifications_base_url",
        DomainSettingUpdate(value_text="https://cloud.example.com"),
    )
    settings_api.upsert_notification_setting(
        db_session,
        "nextcloud_talk_notifications_username",
        DomainSettingUpdate(value_text="Dotmac Notifications"),
    )
    settings_api.upsert_notification_setting(
        db_session,
        "nextcloud_talk_notifications_app_password",
        DomainSettingUpdate(value_text="secret"),
    )
    settings_api.upsert_notification_setting(
        db_session,
        "nextcloud_talk_notifications_room_type",
        DomainSettingUpdate(value_text="1"),
    )


def test_forward_agent_notification_reuses_room(db_session, person, monkeypatch):
    _configure_talk_notifications(db_session)

    created = {"count": 0}
    sent = {"messages": []}

    def _fake_create_room(self, invite, room_type=1):
        created["count"] += 1
        assert invite == "Test User"
        assert room_type == 1
        return {"token": "room-token-1"}

    def _fake_post_message(self, room_token, message, options=None):
        sent["messages"].append((room_token, message))
        return {"id": "msg-1"}

    monkeypatch.setattr(
        "app.services.nextcloud_talk.NextcloudTalkClient.create_room_with_invite",
        _fake_create_room,
    )
    monkeypatch.setattr(
        "app.services.nextcloud_talk.NextcloudTalkClient.post_message",
        _fake_post_message,
    )

    payload = {"title": "Mentioned", "subtitle": "Inbox", "preview": "You were mentioned", "kind": "mention"}
    first = nextcloud_talk_notifications.forward_agent_notification(
        db_session,
        person_id=str(person.id),
        payload=payload,
    )
    second = nextcloud_talk_notifications.forward_agent_notification(
        db_session,
        person_id=str(person.id),
        payload=payload,
    )

    assert first is True
    assert second is True
    assert created["count"] == 1
    assert len(sent["messages"]) == 2
    assert sent["messages"][0][0] == "room-token-1"
    assert sent["messages"][1][0] == "room-token-1"

    rows = db_session.query(NextcloudTalkNotificationRoom).all()
    assert len(rows) == 1
    assert rows[0].person_id == person.id
    assert rows[0].room_token == "room-token-1"


def test_forward_agent_notification_skips_when_disabled(db_session, person, monkeypatch):
    called = {"create": 0, "post": 0}

    def _fake_create_room(self, invite, room_type=1):
        called["create"] += 1
        return {"token": "room-token-1"}

    def _fake_post_message(self, room_token, message, options=None):
        called["post"] += 1
        return {"id": "msg-1"}

    monkeypatch.setattr(
        "app.services.nextcloud_talk.NextcloudTalkClient.create_room_with_invite",
        _fake_create_room,
    )
    monkeypatch.setattr(
        "app.services.nextcloud_talk.NextcloudTalkClient.post_message",
        _fake_post_message,
    )

    forwarded = nextcloud_talk_notifications.forward_agent_notification(
        db_session,
        person_id=str(person.id),
        payload={"title": "Reminder"},
    )

    assert forwarded is False
    assert called["create"] == 0
    assert called["post"] == 0


def test_forward_stored_notification_uses_email_recipient(db_session, person, monkeypatch):
    _configure_talk_notifications(db_session)

    notification = Notification(
        channel=NotificationChannel.push,
        recipient=person.email,
        subject="Ticket Update",
        body="A new internal update is available.",
        status=NotificationStatus.delivered,
    )
    db_session.add(notification)
    db_session.commit()
    db_session.refresh(notification)

    calls = {"create": 0, "post": 0}

    def _fake_create_room(self, invite, room_type=1):
        calls["create"] += 1
        return {"token": "room-token-2"}

    def _fake_post_message(self, room_token, message, options=None):
        calls["post"] += 1
        assert "Ticket Update" in message
        return {"id": "msg-2"}

    monkeypatch.setattr(
        "app.services.nextcloud_talk.NextcloudTalkClient.create_room_with_invite",
        _fake_create_room,
    )
    monkeypatch.setattr(
        "app.services.nextcloud_talk.NextcloudTalkClient.post_message",
        _fake_post_message,
    )

    forwarded = nextcloud_talk_notifications.forward_stored_notification(
        db_session,
        notification=notification,
    )

    assert forwarded is True
    assert calls["create"] == 1
    assert calls["post"] == 1


def test_send_test_message_with_saved_settings(db_session, monkeypatch):
    _configure_talk_notifications(db_session)
    calls = {"create": 0, "post": 0}

    def _fake_create_room(self, invite, room_type=1):
        calls["create"] += 1
        assert invite == "Confidence Okaka"
        assert room_type == 1
        return {"token": "room-token-test"}

    def _fake_post_message(self, room_token, message, options=None):
        calls["post"] += 1
        assert room_token == "room-token-test"
        assert "hello from test" in message
        return {"id": "msg-test"}

    monkeypatch.setattr(
        "app.services.nextcloud_talk.NextcloudTalkClient.create_room_with_invite",
        _fake_create_room,
    )
    monkeypatch.setattr(
        "app.services.nextcloud_talk.NextcloudTalkClient.post_message",
        _fake_post_message,
    )

    ok, result = nextcloud_talk_notifications.send_test_message(
        db_session,
        invite_target="Confidence Okaka",
        message="hello from test",
    )

    assert ok is True
    assert "Test message sent to Confidence Okaka." in result
    assert calls["create"] == 1
    assert calls["post"] == 1


def test_forward_agent_notification_recreates_room_on_stale_cached_token(db_session, person, monkeypatch):
    _configure_talk_notifications(db_session)
    db_session.add(
        NextcloudTalkNotificationRoom(
            person_id=person.id,
            base_url="https://cloud.example.com",
            notifier_username="Dotmac Notifications",
            invite_target="Test User",
            room_token="old-room-token",
        )
    )
    db_session.commit()

    calls = {"create": 0, "post": 0}

    def _fake_create_room(self, invite, room_type=1):
        calls["create"] += 1
        assert invite == "Test User"
        return {"token": "new-room-token"}

    def _fake_post_message(self, room_token, message, options=None):
        calls["post"] += 1
        if calls["post"] == 1:
            assert room_token == "old-room-token"
            raise NextcloudTalkError("HTTP error: 404")
        assert room_token == "new-room-token"
        return {"id": "msg-3"}

    monkeypatch.setattr(
        "app.services.nextcloud_talk.NextcloudTalkClient.create_room_with_invite",
        _fake_create_room,
    )
    monkeypatch.setattr(
        "app.services.nextcloud_talk.NextcloudTalkClient.post_message",
        _fake_post_message,
    )

    forwarded = nextcloud_talk_notifications.forward_agent_notification(
        db_session,
        person_id=str(person.id),
        payload={"title": "Ticket Assigned"},
    )

    assert forwarded is True
    assert calls["create"] == 1
    assert calls["post"] == 2
    room = db_session.query(NextcloudTalkNotificationRoom).filter_by(person_id=person.id).one()
    assert room.room_token == "new-room-token"


def test_clear_cached_rooms_filters_by_instance(db_session, person):
    db_session.add_all(
        [
            NextcloudTalkNotificationRoom(
                person_id=person.id,
                base_url="https://cloud.example.com",
                notifier_username="user-a",
                invite_target="Test User",
                room_token="room-a",
            ),
            NextcloudTalkNotificationRoom(
                person_id=person.id,
                base_url="https://cloud.example.com",
                notifier_username="user-b",
                invite_target="Test User",
                room_token="room-b",
            ),
        ]
    )
    db_session.commit()

    cleared = nextcloud_talk_notifications.clear_cached_rooms(
        db_session,
        base_url="https://cloud.example.com",
        notifier_username="user-a",
    )

    assert cleared == 1
    remaining = db_session.query(NextcloudTalkNotificationRoom).all()
    assert len(remaining) == 1
    assert remaining[0].notifier_username == "user-b"
