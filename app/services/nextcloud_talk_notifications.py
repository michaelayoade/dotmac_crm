from __future__ import annotations

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models.domain_settings import SettingDomain
from app.models.nextcloud_talk_notification import NextcloudTalkNotificationRoom
from app.models.notification import Notification
from app.models.person import Person
from app.services.common import coerce_uuid
from app.services.nextcloud_talk import NextcloudTalkClient, NextcloudTalkError
from app.services.settings_spec import resolve_value

logger = get_logger(__name__)


def _as_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: object, *, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _resolve_notification_config(db: Session) -> dict[str, object] | None:
    def _value(key: str):
        value = resolve_value(db, SettingDomain.notification, key)
        if value in (None, ""):
            # Backward compatibility with earlier storage in comms domain.
            value = resolve_value(db, SettingDomain.comms, key)
        return value

    enabled = _as_bool(_value("nextcloud_talk_notifications_enabled"), default=False)
    if not enabled:
        return None
    base_url = str(_value("nextcloud_talk_notifications_base_url") or "").strip()
    username = str(_value("nextcloud_talk_notifications_username") or "").strip()
    app_password = str(_value("nextcloud_talk_notifications_app_password") or "").strip()
    room_type = _as_int(
        _value("nextcloud_talk_notifications_room_type"),
        default=1,
    )
    if not base_url or not username or not app_password:
        logger.debug("talk_notification_config_incomplete")
        return None
    return {
        "base_url": base_url.rstrip("/"),
        "username": username,
        "app_password": app_password,
        "room_type": max(1, room_type),
    }


def _resolve_raw_notification_settings(db: Session) -> dict[str, object]:
    def _value(key: str):
        value = resolve_value(db, SettingDomain.notification, key)
        if value in (None, ""):
            value = resolve_value(db, SettingDomain.comms, key)
        return value

    return {
        "enabled": _as_bool(_value("nextcloud_talk_notifications_enabled"), default=False),
        "base_url": str(_value("nextcloud_talk_notifications_base_url") or "").strip().rstrip("/"),
        "username": str(_value("nextcloud_talk_notifications_username") or "").strip(),
        "app_password": str(_value("nextcloud_talk_notifications_app_password") or "").strip(),
    }


def notification_settings_fingerprint(db: Session) -> tuple[bool, str, str, str]:
    raw = _resolve_raw_notification_settings(db)
    return (
        bool(raw["enabled"]),
        str(raw["base_url"]),
        str(raw["username"]),
        str(raw["app_password"]),
    )


def _resolve_invite_target(person: Person) -> str | None:
    if isinstance(person.display_name, str) and person.display_name.strip():
        return person.display_name.strip()
    full_name = f"{person.first_name} {person.last_name}".strip()
    return full_name or None


def _resolve_person_by_recipient(db: Session, recipient: str) -> Person | None:
    raw = (recipient or "").strip()
    if not raw:
        return None
    try:
        person = db.get(Person, coerce_uuid(raw))
    except Exception:
        person = None
    if person:
        return person
    return db.query(Person).filter(Person.email == raw).first()


def _extract_room_token(room_payload: dict) -> str | None:
    candidates = (
        room_payload.get("token"),
        room_payload.get("roomToken"),
        room_payload.get("id"),
        room_payload.get("roomid"),
    )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _render_agent_notification_message(payload: dict) -> str:
    title = str(payload.get("title") or "Notification").strip()
    subtitle = str(payload.get("subtitle") or "").strip()
    preview = str(payload.get("preview") or "").strip()
    kind = str(payload.get("kind") or "").strip()

    parts: list[str] = []
    headline = title if not subtitle else f"{title}: {subtitle}"
    if headline:
        parts.append(headline)
    if preview:
        parts.append(preview)
    if kind:
        parts.append(f"Type: {kind}")
    return "\n".join(parts).strip() or "Notification"


def _render_stored_notification_message(notification: Notification) -> str:
    subject = (notification.subject or "Notification").strip()
    body = (notification.body or "").strip()
    if body:
        return f"{subject}\n{body}"
    return subject


def _resolve_room_token(
    db: Session,
    *,
    client: NextcloudTalkClient,
    person: Person,
    invite_target: str,
    base_url: str,
    notifier_username: str,
    room_type: int,
) -> str:
    mapping = (
        db.query(NextcloudTalkNotificationRoom)
        .filter(NextcloudTalkNotificationRoom.person_id == person.id)
        .filter(NextcloudTalkNotificationRoom.base_url == base_url)
        .filter(NextcloudTalkNotificationRoom.notifier_username == notifier_username)
        .first()
    )
    if mapping and mapping.room_token:
        if mapping.invite_target != invite_target:
            mapping.invite_target = invite_target
            db.commit()
        return mapping.room_token

    room_payload = client.create_room_with_invite(invite=invite_target, room_type=room_type)
    token = _extract_room_token(room_payload)
    if not token:
        raise NextcloudTalkError("Unable to resolve room token from Nextcloud Talk response")

    if mapping:
        mapping.room_token = token
        mapping.invite_target = invite_target
    else:
        db.add(
            NextcloudTalkNotificationRoom(
                person_id=person.id,
                base_url=base_url,
                notifier_username=notifier_username,
                invite_target=invite_target,
                room_token=token,
            )
        )
    db.commit()
    return token


def clear_cached_rooms(
    db: Session,
    *,
    base_url: str | None = None,
    notifier_username: str | None = None,
) -> int:
    query = db.query(NextcloudTalkNotificationRoom)
    if base_url:
        query = query.filter(NextcloudTalkNotificationRoom.base_url == base_url.rstrip("/"))
    if notifier_username:
        query = query.filter(NextcloudTalkNotificationRoom.notifier_username == notifier_username.strip())
    deleted = query.delete(synchronize_session=False)
    db.commit()
    return int(deleted or 0)


def _clear_person_room_cache(
    db: Session,
    *,
    person: Person,
    base_url: str,
    notifier_username: str,
) -> None:
    (
        db.query(NextcloudTalkNotificationRoom)
        .filter(NextcloudTalkNotificationRoom.person_id == person.id)
        .filter(NextcloudTalkNotificationRoom.base_url == base_url)
        .filter(NextcloudTalkNotificationRoom.notifier_username == notifier_username)
        .delete(synchronize_session=False)
    )
    db.commit()


def _is_stale_room_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "http error: 404" in text or "http error: 403" in text or "ocs error 404" in text or "ocs error 403" in text


def _send_to_person(db: Session, *, person: Person, message: str) -> bool:
    config = _resolve_notification_config(db)
    if not config:
        return False

    invite_target = _resolve_invite_target(person)
    if not invite_target:
        logger.info("talk_notification_skip_missing_invite person_id=%s", person.id)
        return False

    client = NextcloudTalkClient(
        base_url=str(config["base_url"]),
        username=str(config["username"]),
        app_password=str(config["app_password"]),
        db=db,
    )
    room_type = _as_int(config.get("room_type"), default=1)
    try:
        room_token = _resolve_room_token(
            db,
            client=client,
            person=person,
            invite_target=invite_target,
            base_url=str(config["base_url"]),
            notifier_username=str(config["username"]),
            room_type=room_type,
        )
        try:
            client.post_message(room_token=room_token, message=message)
            logger.info("talk_notification_forwarded person_id=%s room_token=%s", person.id, room_token)
            return True
        except NextcloudTalkError as exc:
            if not _is_stale_room_error(exc):
                raise
            logger.info(
                "talk_notification_stale_room_detected person_id=%s room_token=%s error=%s",
                person.id,
                room_token,
                exc,
            )
            _clear_person_room_cache(
                db,
                person=person,
                base_url=str(config["base_url"]),
                notifier_username=str(config["username"]),
            )
            retry_room_token = _resolve_room_token(
                db,
                client=client,
                person=person,
                invite_target=invite_target,
                base_url=str(config["base_url"]),
                notifier_username=str(config["username"]),
                room_type=room_type,
            )
            client.post_message(room_token=retry_room_token, message=message)
            logger.info("talk_notification_forwarded person_id=%s room_token=%s retry=1", person.id, retry_room_token)
            return True
    except Exception as exc:
        logger.warning("talk_notification_forward_failed person_id=%s error=%s", person.id, exc)
        db.rollback()
        return False


def forward_agent_notification(db: Session, *, person_id: str, payload: dict) -> bool:
    try:
        person = db.get(Person, coerce_uuid(person_id))
    except Exception:
        return False
    if not person or not person.is_active:
        return False
    return _send_to_person(
        db,
        person=person,
        message=_render_agent_notification_message(payload),
    )


def forward_stored_notification(db: Session, *, notification: Notification) -> bool:
    person = _resolve_person_by_recipient(db, notification.recipient)
    if not person or not person.is_active:
        return False
    return _send_to_person(
        db,
        person=person,
        message=_render_stored_notification_message(notification),
    )


def send_test_message(
    db: Session,
    *,
    invite_target: str,
    message: str | None = None,
) -> tuple[bool, str]:
    config = _resolve_notification_config(db)
    if not config:
        return False, "Talk notification settings are missing or disabled."
    invite_value = (invite_target or "").strip()
    if not invite_value:
        return False, "Target username/full name is required."
    body = (message or "").strip() or "Dotmac Talk notification test message."
    client = NextcloudTalkClient(
        base_url=str(config["base_url"]),
        username=str(config["username"]),
        app_password=str(config["app_password"]),
        db=db,
    )
    room_type = _as_int(config.get("room_type"), default=1)
    try:
        room_payload = client.create_room_with_invite(
            invite=invite_value,
            room_type=room_type,
        )
        room_token = _extract_room_token(room_payload)
        if not room_token:
            return False, "Talk room created but room token was not returned."
        client.post_message(room_token=room_token, message=body)
        logger.info("talk_notification_test_sent invite=%s room_token=%s", invite_value, room_token)
        return True, f"Test message sent to {invite_value}."
    except Exception as exc:
        logger.warning("talk_notification_test_failed invite=%s error=%s", invite_value, exc)
        return False, str(exc)
