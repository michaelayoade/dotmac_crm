"""Reporting-period calculation shared by Weekly Reporting orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

LAGOS = ZoneInfo("Africa/Lagos")


def previous_complete_week(now: datetime | None = None) -> tuple[datetime, datetime, datetime, datetime]:
    """Return the previous Monday-to-Monday window in Lagos and UTC."""
    local_now = (now or datetime.now(UTC)).astimezone(LAGOS)
    current_monday = local_now.date() - timedelta(days=local_now.weekday())
    start_local = datetime.combine(current_monday - timedelta(days=7), time.min, tzinfo=LAGOS)
    end_exclusive_local = start_local + timedelta(days=7)
    return start_local, end_exclusive_local, start_local.astimezone(UTC), end_exclusive_local.astimezone(UTC)


def period_details(now: datetime) -> tuple[str, str]:
    start_local, end_exclusive_local, _, _ = previous_complete_week(now)
    end_local = end_exclusive_local - timedelta(seconds=1)
    period = f"{start_local:%d %B %Y} - {end_local:%d %B %Y}"
    slug = f"{start_local:%Y-%m-%d}_to_{end_local:%Y-%m-%d}"
    return period, slug
