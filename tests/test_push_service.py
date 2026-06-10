"""Tests for the mobile push pipeline (device registry + FCM sender)."""

import pytest
from fastapi import HTTPException

from app.models.field import DevicePlatform, DeviceToken
from app.models.notification import (
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationStatus,
)
from app.services import push as push_module
from app.services.push import push_devices, push_sender

FAKE_ACCOUNT = {"client_email": "svc@test.iam", "private_key": "key", "project_id": "test-proj"}


@pytest.fixture()
def configured_fcm(monkeypatch):
    """Pretend FCM is configured; capture sends instead of calling Google."""
    sent: list[dict] = []

    def _fake_send(account, fcm_token, title, body, data):
        sent.append({"token": fcm_token, "title": title, "body": body, "data": data})
        return "projects/test-proj/messages/123"

    monkeypatch.setattr(push_module, "_load_service_account", lambda: FAKE_ACCOUNT)
    monkeypatch.setattr(push_module, "_send_fcm_message", _fake_send)
    return sent


def test_register_device_and_refresh(db_session, person):
    device = push_devices.register(
        db_session, platform="android", fcm_token="tok-1", person_id=str(person.id), app_version="1.0.0"
    )
    assert device.platform == DevicePlatform.android
    assert device.person_id == person.id

    # Re-registering the same token updates rather than duplicates.
    again = push_devices.register(
        db_session, platform="android", fcm_token="tok-1", person_id=str(person.id), app_version="1.1.0"
    )
    assert again.id == device.id
    assert again.app_version == "1.1.0"
    assert db_session.query(DeviceToken).filter(DeviceToken.fcm_token == "tok-1").count() == 1


def test_register_requires_exactly_one_owner(db_session, person):
    with pytest.raises(HTTPException) as exc:
        push_devices.register(db_session, platform="android", fcm_token="tok-x")
    assert exc.value.status_code == 422


def test_register_rejects_bad_platform(db_session, person):
    with pytest.raises(HTTPException):
        push_devices.register(db_session, platform="windows", fcm_token="tok-x", person_id=str(person.id))


def test_send_to_person_delivers_and_audits(db_session, person, configured_fcm):
    push_devices.register(db_session, platform="android", fcm_token="tok-1", person_id=str(person.id))
    push_devices.register(db_session, platform="ios", fcm_token="tok-2", person_id=str(person.id))

    results = push_sender.send_to_person(
        db_session, str(person.id), title="New job assigned", body="WO-1", data={"work_order_id": "abc"}
    )
    assert results["sent"] == 2

    notification = db_session.query(Notification).filter(Notification.channel == NotificationChannel.push).one()
    assert notification.status == NotificationStatus.delivered
    deliveries = db_session.query(NotificationDelivery).filter_by(notification_id=notification.id).all()
    assert len(deliveries) == 2
    assert all(d.status == DeliveryStatus.delivered for d in deliveries)
    assert {s["token"] for s in configured_fcm} == {"tok-1", "tok-2"}


def test_duplicate_push_is_skipped(db_session, person, configured_fcm):
    push_devices.register(db_session, platform="android", fcm_token="tok-1", person_id=str(person.id))

    first = push_sender.send_to_person(db_session, str(person.id), title="New job assigned", body="WO-1")
    replay = push_sender.send_to_person(db_session, str(person.id), title="New job assigned", body="WO-1")
    assert first["sent"] == 1
    assert replay["skipped"] == 1
    assert len(configured_fcm) == 1


def test_invalid_token_is_pruned(db_session, person, monkeypatch):
    monkeypatch.setattr(push_module, "_load_service_account", lambda: FAKE_ACCOUNT)

    def _unregistered(account, fcm_token, title, body, data):
        raise push_module._TokenInvalid("UNREGISTERED")

    monkeypatch.setattr(push_module, "_send_fcm_message", _unregistered)
    device = push_devices.register(db_session, platform="android", fcm_token="tok-dead", person_id=str(person.id))

    results = push_sender.send_to_person(db_session, str(person.id), title="T", body="B")
    assert results["pruned"] == 1
    db_session.refresh(device)
    assert device.is_active is False


def test_unconfigured_fcm_records_failed_delivery(db_session, person, monkeypatch):
    monkeypatch.setattr(push_module, "_load_service_account", lambda: None)
    push_devices.register(db_session, platform="android", fcm_token="tok-1", person_id=str(person.id))

    results = push_sender.send_to_person(db_session, str(person.id), title="T", body="B")
    assert results["failed"] == 1
    notification = db_session.query(Notification).filter(Notification.channel == NotificationChannel.push).one()
    assert notification.status == NotificationStatus.failed


def test_no_devices_is_a_noop(db_session, person, configured_fcm):
    results = push_sender.send_to_person(db_session, str(person.id), title="T", body="B")
    assert results["skipped"] == 1
    assert configured_fcm == []


def test_work_order_assignment_triggers_push(db_session, person, configured_fcm, monkeypatch):
    """Creating an assigned work order must enqueue (or sync-send) a push."""
    from app.schemas.workforce import WorkOrderCreate
    from app.services.workforce import work_orders

    # No Celery broker in tests: the queue helper falls back to sync send.
    work_order = work_orders.create(
        db_session,
        WorkOrderCreate(title="Install fiber", assigned_to_person_id=person.id),
    )
    assert work_order.assigned_to_person_id == person.id

    push_devices.register(db_session, platform="android", fcm_token="tok-1", person_id=str(person.id))
    push_module.queue_work_order_assignment_push(db_session, work_order)

    assert any(s["data"]["work_order_id"] == str(work_order.id) for s in configured_fcm)
