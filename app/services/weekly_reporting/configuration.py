"""Notification-domain configuration for automated weekly reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain, SettingValueType
from app.schemas.settings import DomainSettingUpdate
from app.services import settings_spec
from app.services.domain_settings import notification_settings

ENABLED_KEY = "weekly_reporting_enabled"
RECIPIENTS_KEY = "weekly_reporting_recipients"
SCHEDULE_DAY_KEY = "weekly_reporting_schedule_day"
SCHEDULE_TIME_KEY = "weekly_reporting_schedule_time"
TIMEZONE_KEY = "weekly_reporting_timezone"
CUSTOM_SETTING_KEYS = frozenset(
    {
        ENABLED_KEY,
        RECIPIENTS_KEY,
        SCHEDULE_DAY_KEY,
        SCHEDULE_TIME_KEY,
        TIMEZONE_KEY,
    }
)

DEFAULT_SCHEDULE_DAY = "monday"
DEFAULT_SCHEDULE_TIME = "08:00"
DEFAULT_TIMEZONE = "Africa/Lagos"
WEEKDAY_OPTIONS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_EMAIL_ADAPTER = TypeAdapter(EmailStr)


@dataclass(frozen=True)
class WeeklyReportingConfig:
    enabled: bool
    recipients: tuple[str, ...]
    schedule_day: str
    schedule_time: str
    timezone: str


def _validate_email(value: str) -> str:
    candidate = str(value or "").strip()
    try:
        return str(_EMAIL_ADAPTER.validate_python(candidate)).lower()
    except ValidationError as exc:
        raise ValueError("Enter a valid recipient email address.") from exc


def _normalize_recipients(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise ValueError("Weekly Reporting recipients must be stored as a JSON list.")
    recipients: list[str] = []
    seen: set[str] = set()
    for item in value:
        email = _validate_email(str(item))
        key = email.casefold()
        if key not in seen:
            recipients.append(email)
            seen.add(key)
    return tuple(recipients)


def _validate_schedule_day(value: object) -> str:
    day = str(value or DEFAULT_SCHEDULE_DAY).strip().lower()
    if day not in WEEKDAY_OPTIONS:
        raise ValueError("Select a valid weekly reporting day.")
    return day


def _validate_schedule_time(value: object) -> str:
    raw = str(value or DEFAULT_SCHEDULE_TIME).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            continue
    raise ValueError("Enter a valid weekly reporting time in HH:MM format.")


def _validate_timezone(value: object) -> str:
    timezone = str(value or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Enter a valid IANA timezone, for example Africa/Lagos.") from exc
    return timezone


def load_configuration(db: Session) -> WeeklyReportingConfig:
    enabled = bool(settings_spec.resolve_value(db, SettingDomain.notification, ENABLED_KEY))
    recipients = _normalize_recipients(settings_spec.resolve_value(db, SettingDomain.notification, RECIPIENTS_KEY))
    schedule_day = _validate_schedule_day(settings_spec.resolve_value(db, SettingDomain.notification, SCHEDULE_DAY_KEY))
    schedule_time = _validate_schedule_time(
        settings_spec.resolve_value(db, SettingDomain.notification, SCHEDULE_TIME_KEY)
    )
    timezone = _validate_timezone(settings_spec.resolve_value(db, SettingDomain.notification, TIMEZONE_KEY))
    return WeeklyReportingConfig(
        enabled=enabled,
        recipients=recipients,
        schedule_day=schedule_day,
        schedule_time=schedule_time,
        timezone=timezone,
    )


def get_settings_snapshot(db: Session) -> dict[str, Any]:
    config = load_configuration(db)
    return {
        "enabled": config.enabled,
        "recipients": list(config.recipients),
        "recipient_count": len(config.recipients),
        "schedule_day": config.schedule_day,
        "schedule_time": config.schedule_time,
        "timezone": config.timezone,
        "weekday_options": WEEKDAY_OPTIONS,
    }


def _payload(
    value_type: SettingValueType,
    *,
    value_text: str | None = None,
    value_json: list[str] | None = None,
) -> DomainSettingUpdate:
    return DomainSettingUpdate(
        value_type=value_type,
        value_text=value_text,
        value_json=value_json,
        is_active=True,
    )


def save_schedule(
    db: Session,
    *,
    enabled: bool,
    schedule_day: str,
    schedule_time: str,
    timezone: str,
) -> dict[str, Any]:
    day = _validate_schedule_day(schedule_day)
    local_time = _validate_schedule_time(schedule_time)
    timezone_value = _validate_timezone(timezone)
    notification_settings.upsert_by_key(
        db,
        ENABLED_KEY,
        _payload(SettingValueType.boolean, value_text="true" if enabled else "false"),
    )
    notification_settings.upsert_by_key(
        db,
        SCHEDULE_DAY_KEY,
        _payload(SettingValueType.string, value_text=day),
    )
    notification_settings.upsert_by_key(
        db,
        SCHEDULE_TIME_KEY,
        _payload(SettingValueType.string, value_text=local_time),
    )
    notification_settings.upsert_by_key(
        db,
        TIMEZONE_KEY,
        _payload(SettingValueType.string, value_text=timezone_value),
    )
    return get_settings_snapshot(db)


def _save_recipients(db: Session, recipients: list[str]) -> dict[str, Any]:
    normalized = list(_normalize_recipients(recipients))
    notification_settings.upsert_by_key(
        db,
        RECIPIENTS_KEY,
        _payload(SettingValueType.json, value_json=normalized),
    )
    return get_settings_snapshot(db)


def add_recipient(db: Session, email: str) -> dict[str, Any]:
    candidate = _validate_email(email)
    recipients = list(load_configuration(db).recipients)
    if candidate.casefold() in {item.casefold() for item in recipients}:
        raise ValueError("That recipient is already configured.")
    recipients.append(candidate)
    return _save_recipients(db, recipients)


def update_recipient(db: Session, index: int, email: str) -> dict[str, Any]:
    candidate = _validate_email(email)
    recipients = list(load_configuration(db).recipients)
    if index < 0 or index >= len(recipients):
        raise ValueError("Weekly Reporting recipient was not found.")
    duplicate_indexes = {
        item_index
        for item_index, item in enumerate(recipients)
        if item.casefold() == candidate.casefold() and item_index != index
    }
    if duplicate_indexes:
        raise ValueError("That recipient is already configured.")
    recipients[index] = candidate
    return _save_recipients(db, recipients)


def remove_recipient(db: Session, index: int) -> dict[str, Any]:
    recipients = list(load_configuration(db).recipients)
    if index < 0 or index >= len(recipients):
        raise ValueError("Weekly Reporting recipient was not found.")
    recipients.pop(index)
    return _save_recipients(db, recipients)
