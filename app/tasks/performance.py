from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models.domain_settings import SettingDomain, SettingValueType
from app.schemas.settings import DomainSettingUpdate
from app.services.domain_settings import performance_settings
from app.services.performance.goals import performance_goals
from app.services.performance.reviews import performance_reviews
from app.services.performance.scoring import ScoreWindow, performance_scoring
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)


def _week_floor(reference: datetime) -> datetime:
    at_utc = reference.astimezone(UTC)
    monday = at_utc - timedelta(days=at_utc.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _latest_completed_window(reference: datetime | None = None) -> ScoreWindow:
    now = (reference or datetime.now(UTC)).astimezone(UTC)
    end_at = _week_floor(now)
    start_at = end_at - timedelta(days=7)
    return ScoreWindow(start_at=start_at, end_at=end_at)


def _parse_iso_datetime(value: object | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _load_checkpoint_end(session) -> datetime | None:
    value = resolve_value(session, SettingDomain.performance, "last_scored_period_end")
    return _parse_iso_datetime(value)


def _store_checkpoint_end(session, period_end: datetime) -> None:
    payload = DomainSettingUpdate(
        value_type=SettingValueType.string,
        value_text=period_end.astimezone(UTC).isoformat(),
    )
    performance_settings.upsert_by_key(session, "last_scored_period_end", payload)


def _windows_to_process(last_end: datetime | None, latest_window: ScoreWindow) -> list[ScoreWindow]:
    if last_end is None:
        return [latest_window]

    if last_end >= latest_window.end_at:
        return []

    windows: list[ScoreWindow] = []
    cursor_end = last_end
    while cursor_end < latest_window.end_at:
        start_at = cursor_end
        end_at = start_at + timedelta(days=7)
        windows.append(ScoreWindow(start_at=start_at, end_at=end_at))
        cursor_end = end_at
    return windows


@celery_app.task(name="app.tasks.performance.compute_weekly_scores")
def compute_weekly_scores(period_start_iso: str | None = None, period_end_iso: str | None = None) -> dict:
    session = SessionLocal()
    try:
        if period_start_iso and period_end_iso:
            period_start = _parse_iso_datetime(period_start_iso)
            period_end = _parse_iso_datetime(period_end_iso)
            if not period_start or not period_end or period_start >= period_end:
                raise ValueError("Invalid period range")
            windows = [ScoreWindow(start_at=period_start, end_at=period_end)]
        else:
            latest_window = _latest_completed_window()
            checkpoint_end = _load_checkpoint_end(session)
            windows = _windows_to_process(checkpoint_end, latest_window)

        if not windows:
            return {"processed_windows": 0, "total_processed": 0, "periods": []}

        periods: list[dict] = []
        total_processed = 0
        for window in windows:
            result = performance_scoring.compute_period(session, window)
            total_processed += int(result.get("processed") or 0)
            periods.append({"start_at": window.start_at.isoformat(), "end_at": window.end_at.isoformat()})
            _store_checkpoint_end(session, window.end_at)
            generate_flagged_reviews.delay(window.start_at.isoformat(), window.end_at.isoformat())

        return {"processed_windows": len(windows), "total_processed": total_processed, "periods": periods}
    except Exception:
        session.rollback()
        logger.exception("Failed to compute weekly performance scores")
        raise
    finally:
        session.close()


@celery_app.task(name="app.tasks.performance.generate_flagged_reviews")
def generate_flagged_reviews(period_start_iso: str | None = None, period_end_iso: str | None = None) -> dict:
    session = SessionLocal()
    try:
        if period_start_iso and period_end_iso:
            period_start = _parse_iso_datetime(period_start_iso)
            period_end = _parse_iso_datetime(period_end_iso)
            if not period_start or not period_end or period_start >= period_end:
                raise ValueError("Invalid period range")
        else:
            latest_window = _latest_completed_window()
            period_start = latest_window.start_at
            period_end = latest_window.end_at

        generated = performance_reviews.generate_flagged_reviews_for_period(session, period_start, period_end)
        return {
            "generated": generated,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
        }
    except Exception:
        session.rollback()
        logger.exception("Failed to generate flagged performance reviews")
        raise
    finally:
        session.close()


@celery_app.task(name="app.tasks.performance.update_goal_progress")
def update_goal_progress() -> dict:
    session = SessionLocal()
    try:
        updated = performance_goals.refresh_progress(session)
        return {"updated": updated}
    except Exception:
        session.rollback()
        logger.exception("Failed to refresh performance goal progress")
        raise
    finally:
        session.close()
