"""Shift window helpers for CRM reports/time tracking.

Shift rules (per user request):
- Day shift:   08:00 -> 17:00
- Night shift: 17:00 -> 08:00 (overnight)

All datetimes returned are timezone-aware.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services import domain_settings as settings_service
from app.services.settings_cache import SettingsCache


@dataclass(frozen=True)
class ShiftWindow:
    name: str  # "day" | "night"
    tz: str
    start_local: datetime
    end_local: datetime
    start_utc: datetime
    end_utc: datetime


def _safe_zoneinfo(tz_name: str | None) -> ZoneInfo:
    name = (tz_name or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def resolve_company_timezone(db: Session) -> str:
    """Resolve a single 'company timezone' for shift windows.

    Preference order:
    1) Cached scheduler.timezone setting (fast)
    2) DB scheduler.timezone setting
    3) CELERY_TIMEZONE env var
    4) UTC
    """
    cached = SettingsCache.get(SettingDomain.scheduler.value, "timezone")
    if isinstance(cached, str) and cached.strip():
        return cached.strip()

    try:
        setting = settings_service.scheduler_settings.get_by_key(db, "timezone")
        if getattr(setting, "value_text", None):
            return str(setting.value_text).strip() or "UTC"
    except Exception:
        pass

    return (os.getenv("CELERY_TIMEZONE", "") or "UTC").strip() or "UTC"


def current_shift_window(*, now_utc: datetime | None = None, tz_name: str) -> ShiftWindow:
    """Return the active shift window that contains now (in tz_name)."""
    now = now_utc or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    tz = _safe_zoneinfo(tz_name)
    now_local = now.astimezone(tz)

    day_start = now_local.replace(hour=8, minute=0, second=0, microsecond=0)
    day_end = now_local.replace(hour=17, minute=0, second=0, microsecond=0)

    if day_start <= now_local < day_end:
        start_local = day_start
        end_local = day_end
        name = "day"
    else:
        name = "night"
        if now_local >= day_end:
            # Evening -> next day morning
            start_local = day_end
            end_local = day_start + timedelta(days=1)
        else:
            # After midnight -> same day morning (shift started yesterday at 17:00)
            start_local = day_end - timedelta(days=1)
            end_local = day_start

    start_utc = start_local.astimezone(UTC)
    end_utc = end_local.astimezone(UTC)
    return ShiftWindow(
        name=name,
        tz=tz.key,
        start_local=start_local,
        end_local=end_local,
        start_utc=start_utc,
        end_utc=end_utc,
    )

