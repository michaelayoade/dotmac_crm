import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from celery import Celery
from celery.beat import ScheduleEntry
from celery.schedules import crontab

from app import celery_scheduler


def _celery_app() -> Celery:
    app = Celery("db-scheduler-test")
    app.conf.timezone = "Africa/Lagos"
    app.conf.enable_utc = True
    app.conf.beat_refresh_seconds = 30
    return app


def test_refresh_invalidates_celery_heap_and_logs_changed_weekly_cron(monkeypatch, caplog):
    app = _celery_app()
    old_schedule = crontab(minute="13", hour="9", day_of_week="friday", app=app)
    new_schedule = crontab(minute="17", hour="9", day_of_week="friday", app=app)
    scheduler = object.__new__(celery_scheduler.DbScheduler)
    scheduler._last_refresh_at = 0.0
    scheduler._heap = [object()]
    scheduler.app = app
    scheduler.data = {
        "weekly_reporting": ScheduleEntry(
            name="weekly_reporting",
            task="app.tasks.reports.run_weekly_inbound_reporting",
            schedule=old_schedule,
            app=app,
        )
    }
    refreshed_schedule = {
        "weekly_reporting": {
            "task": "app.tasks.reports.run_weekly_inbound_reporting",
            "schedule": new_schedule,
        }
    }
    merged: list[dict] = []

    monkeypatch.setattr(celery_scheduler.time, "monotonic", lambda: 31.0)
    monkeypatch.setattr(
        celery_scheduler,
        "build_beat_schedule",
        lambda *, scheduler_timezone: refreshed_schedule,
    )
    monkeypatch.setattr(scheduler, "merge_inplace", merged.append)

    with caplog.at_level(logging.INFO, logger=celery_scheduler.__name__):
        scheduler._refresh_schedule()

    assert merged == [refreshed_schedule]
    assert scheduler._heap is None
    assert scheduler._last_refresh_at == 31.0
    assert "old_cron=<crontab: 13 9 * * friday" in caplog.text
    assert "new_cron=<crontab: 17 9 * * friday" in caplog.text
    assert "scheduler_timezone=Africa/Lagos" in caplog.text


def test_refresh_does_not_log_unchanged_weekly_cron(monkeypatch, caplog):
    app = _celery_app()
    weekly_schedule = crontab(minute="17", hour="9", day_of_week="friday", app=app)
    scheduler = object.__new__(celery_scheduler.DbScheduler)
    scheduler._last_refresh_at = 0.0
    scheduler._heap = []
    scheduler.app = app
    scheduler.data = {
        "weekly_reporting": ScheduleEntry(
            name="weekly_reporting",
            task="app.tasks.reports.run_weekly_inbound_reporting",
            schedule=weekly_schedule,
            app=app,
        )
    }
    refreshed_schedule = {
        "weekly_reporting": {
            "task": "app.tasks.reports.run_weekly_inbound_reporting",
            "schedule": crontab(minute="17", hour="9", day_of_week="friday", app=app),
        }
    }

    monkeypatch.setattr(celery_scheduler.time, "monotonic", lambda: 31.0)
    monkeypatch.setattr(
        celery_scheduler,
        "build_beat_schedule",
        lambda *, scheduler_timezone: refreshed_schedule,
    )

    with caplog.at_level(logging.INFO, logger=celery_scheduler.__name__):
        scheduler._refresh_schedule()

    assert "CELERY_BEAT_SCHEDULE_CHANGED" not in caplog.text


def test_same_day_reschedule_after_run_is_due_at_new_time():
    app = _celery_app()
    lagos = ZoneInfo("Africa/Lagos")
    now = datetime(2026, 7, 24, 9, 17, tzinfo=lagos)
    last_run_at = datetime(2026, 7, 24, 9, 13, 35, tzinfo=lagos)
    old_schedule = crontab(
        minute="13",
        hour="9",
        day_of_week="friday",
        app=app,
        nowfun=lambda: now,
    )
    new_schedule = crontab(
        minute="17",
        hour="9",
        day_of_week="friday",
        app=app,
        nowfun=lambda: now,
    )
    scheduler = object.__new__(celery_scheduler.DbScheduler)
    scheduler.app = app
    scheduler.data = {
        "weekly_reporting": ScheduleEntry(
            name="weekly_reporting",
            task="app.tasks.reports.run_weekly_inbound_reporting",
            last_run_at=last_run_at,
            schedule=old_schedule,
            app=app,
        )
    }

    scheduler.merge_inplace(
        {
            "weekly_reporting": {
                "task": "app.tasks.reports.run_weekly_inbound_reporting",
                "schedule": new_schedule,
            }
        }
    )

    refreshed_entry = scheduler.schedule["weekly_reporting"]
    assert refreshed_entry.last_run_at == last_run_at
    assert refreshed_entry.is_due().is_due is True
