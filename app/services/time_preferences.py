from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services import settings_spec

DEFAULT_DATE_FORMAT = "%B %d, %Y"
DEFAULT_TIME_FORMAT = "%H:%M"
DEFAULT_WEEK_START = "monday"
DEFAULT_TIMEZONE = "UTC"

ALLOWED_DATE_FORMATS = {
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%B %d, %Y",
}
ALLOWED_TIME_FORMATS = {"%H:%M", "%I:%M %p"}
ALLOWED_WEEK_STARTS = {"monday", "sunday"}


def normalize_timezone(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    return candidate or None


def is_valid_timezone(value: str | None) -> bool:
    candidate = normalize_timezone(value)
    if not candidate:
        return False
    try:
        ZoneInfo(candidate)
        return True
    except Exception:
        return False


def coerce_timezone_or_default(value: str | None, default: str = DEFAULT_TIMEZONE) -> str:
    candidate = normalize_timezone(value)
    if candidate and is_valid_timezone(candidate):
        return candidate
    return default


def resolve_company_timezone(db: Session) -> str:
    resolved = settings_spec.resolve_value(db, SettingDomain.scheduler, "timezone")
    if isinstance(resolved, str) and is_valid_timezone(resolved):
        return resolved.strip()
    return DEFAULT_TIMEZONE


def resolve_company_time_prefs(db: Session) -> tuple[str, str, str, str]:
    timezone = resolve_company_timezone(db)
    date_format = settings_spec.resolve_value(db, SettingDomain.scheduler, "date_format")
    time_format = settings_spec.resolve_value(db, SettingDomain.scheduler, "time_format")
    week_start = settings_spec.resolve_value(db, SettingDomain.scheduler, "week_start")
    safe_date = (
        date_format if isinstance(date_format, str) and date_format in ALLOWED_DATE_FORMATS else DEFAULT_DATE_FORMAT
    )
    safe_time = (
        time_format if isinstance(time_format, str) and time_format in ALLOWED_TIME_FORMATS else DEFAULT_TIME_FORMAT
    )
    safe_week_start = (
        week_start if isinstance(week_start, str) and week_start in ALLOWED_WEEK_STARTS else DEFAULT_WEEK_START
    )
    return timezone, safe_date, safe_time, safe_week_start


def resolve_user_timezone(db: Session, person_timezone: str | None) -> str:
    if is_valid_timezone(person_timezone):
        return str(person_timezone).strip()
    return resolve_company_timezone(db)


def format_datetime_for_user(
    value: datetime | None,
    *,
    db: Session,
    person_timezone: str | None = None,
    include_timezone: bool = False,
) -> str:
    if value is None:
        return "-"
    timezone, date_format, time_format, _ = resolve_company_time_prefs(db)
    effective_timezone = resolve_user_timezone(db, person_timezone) or timezone
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local_value = value.astimezone(ZoneInfo(effective_timezone))
    formatted = local_value.strftime(f"{date_format} at {time_format}")
    if include_timezone:
        return f"{formatted} {effective_timezone}"
    return formatted
