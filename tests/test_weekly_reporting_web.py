from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from celery import Celery

from app.models.domain_settings import SettingDomain
from app.services import scheduler_config
from app.services.weekly_reporting import configuration
from app.tasks import reports as report_tasks
from app.web.admin import system as system_web


def test_notification_settings_context_uses_custom_weekly_reporting_controls(db_session):
    context = system_web._build_settings_context(db_session, "notification")

    assert context["weekly_reporting"]["enabled"] is False
    assert context["weekly_reporting"]["schedule_day"] == "monday"
    assert context["weekly_reporting"]["schedule_time"] == "08:00"
    assert configuration.CUSTOM_SETTING_KEYS.isdisjoint(context["settings_by_key"])
    assert all(section["title"] != "Weekly Reporting" for section in context["sections"])


def test_weekly_reporting_routes_manage_recipients(db_session):
    added = system_web.weekly_reporting_add_recipient("reports@example.com", db_session)
    assert added.status_code == 303
    assert configuration.get_settings_snapshot(db_session)["recipients"] == ["reports@example.com"]

    updated = system_web.weekly_reporting_update_recipient(0, "weekly@example.com", db_session)
    assert updated.status_code == 303
    assert configuration.get_settings_snapshot(db_session)["recipients"] == ["weekly@example.com"]

    removed = system_web.weekly_reporting_remove_recipient(0, db_session)
    assert removed.status_code == 303
    assert configuration.get_settings_snapshot(db_session)["recipients"] == []


def test_weekly_reporting_run_queues_existing_task(monkeypatch):
    queued = []
    monkeypatch.setattr(system_web, "_enqueue_weekly_reporting", lambda: queued.append(True))

    response = system_web.weekly_reporting_run()

    assert response.status_code == 303
    assert queued == [True]
    assert parse_qs(urlparse(response.headers["location"]).query)["weekly_reporting_saved"] == ["run-requested"]


def test_weekly_reporting_enqueue_uses_registered_celery_task(monkeypatch):
    task_result = object()
    monkeypatch.setattr(report_tasks.run_weekly_inbound_reporting, "delay", lambda: task_result)

    assert system_web._enqueue_weekly_reporting() is task_result


def test_weekly_reporting_run_reports_enqueue_failure(monkeypatch):
    def fail_enqueue():
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(system_web, "_enqueue_weekly_reporting", fail_enqueue)

    response = system_web.weekly_reporting_run()

    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["weekly_reporting_error"] == ["Unable to queue Weekly Reporting. Please try again."]


def _assert_due(
    schedule,
    *,
    scheduler_timezone: str,
    now: datetime,
    last_run_at: datetime,
) -> None:
    app = Celery("weekly-reporting-schedule-test")
    app.conf.timezone = scheduler_timezone
    app.conf.enable_utc = True
    schedule.app = app
    schedule.nowfun = lambda: now
    assert schedule.is_due(last_run_at).is_due is True


def test_weekly_reporting_schedule_lagos_time_with_lagos_celery_timezone(db_session):
    schedule = scheduler_config._weekly_reporting_crontab(
        db_session,
        scheduler_timezone="Africa/Lagos",
        now_utc=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
    )

    assert schedule.minute == {0}
    assert schedule.hour == {8}
    assert schedule.day_of_week == {1}
    _assert_due(
        schedule,
        scheduler_timezone="Africa/Lagos",
        now=datetime(2026, 7, 20, 8, 0, tzinfo=ZoneInfo("Africa/Lagos")),
        last_run_at=datetime(2026, 7, 13, 8, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    )


def test_weekly_reporting_utc_report_time_is_converted_to_lagos_celery_timezone(db_session):
    configuration.save_schedule(
        db_session,
        enabled=True,
        schedule_day="friday",
        schedule_time="08:17",
        timezone="UTC",
    )

    schedule = scheduler_config._weekly_reporting_crontab(
        db_session,
        scheduler_timezone="Africa/Lagos",
        now_utc=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
    )

    assert schedule.minute == {17}
    assert schedule.hour == {9}
    assert schedule.day_of_week == {5}
    _assert_due(
        schedule,
        scheduler_timezone="Africa/Lagos",
        now=datetime(2026, 7, 24, 9, 17, tzinfo=ZoneInfo("Africa/Lagos")),
        last_run_at=datetime(2026, 7, 24, 9, 13, 35, tzinfo=ZoneInfo("Africa/Lagos")),
    )
    specs = {spec.key for spec in system_web._generic_settings_specs(SettingDomain.notification)}
    assert configuration.CUSTOM_SETTING_KEYS.isdisjoint(specs)


def test_weekly_reporting_conversion_handles_weekday_and_dst_boundaries(db_session):
    configuration.save_schedule(
        db_session,
        enabled=True,
        schedule_day="monday",
        schedule_time="00:30",
        timezone="Pacific/Kiritimati",
    )
    weekday_boundary = scheduler_config._weekly_reporting_crontab(
        db_session,
        scheduler_timezone="UTC",
        now_utc=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )

    assert weekday_boundary.minute == {30}
    assert weekday_boundary.hour == {10}
    assert weekday_boundary.day_of_week == {0}
    _assert_due(
        weekday_boundary,
        scheduler_timezone="UTC",
        now=datetime(2026, 7, 19, 10, 30, tzinfo=UTC),
        last_run_at=datetime(2026, 7, 12, 10, 30, tzinfo=UTC),
    )

    configuration.save_schedule(
        db_session,
        enabled=True,
        schedule_day="sunday",
        schedule_time="09:00",
        timezone="America/New_York",
    )
    before_dst = scheduler_config._weekly_reporting_crontab(
        db_session,
        scheduler_timezone="UTC",
        now_utc=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
    )
    after_dst = scheduler_config._weekly_reporting_crontab(
        db_session,
        scheduler_timezone="UTC",
        now_utc=datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
    )

    assert before_dst.hour == {14}
    assert before_dst.day_of_week == {0}
    assert after_dst.hour == {13}
    assert after_dst.day_of_week == {0}
    _assert_due(
        before_dst,
        scheduler_timezone="UTC",
        now=datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
        last_run_at=datetime(2026, 2, 22, 14, 0, tzinfo=UTC),
    )
    _assert_due(
        after_dst,
        scheduler_timezone="UTC",
        now=datetime(2026, 3, 15, 13, 0, tzinfo=UTC),
        last_run_at=datetime(2026, 3, 8, 13, 0, tzinfo=UTC),
    )


def test_weekly_reporting_schedule_has_no_parallel_environment_override(db_session, monkeypatch):
    configuration.save_schedule(
        db_session,
        enabled=True,
        schedule_day="wednesday",
        schedule_time="09:30",
        timezone="Africa/Lagos",
    )
    monkeypatch.setenv("WEEKLY_REPORTING_SCHEDULE_DAY", "friday")
    monkeypatch.setenv("WEEKLY_REPORTING_SCHEDULE_TIME", "23:45")
    monkeypatch.setenv("WEEKLY_REPORTING_TIMEZONE", "UTC")

    schedule = scheduler_config._weekly_reporting_crontab(
        db_session,
        scheduler_timezone="Africa/Lagos",
    )

    assert schedule.minute == {30}
    assert schedule.hour == {9}
    assert schedule.day_of_week == {3}


def test_weekly_reporting_task_is_registered_with_existing_celery_beat(db_session, monkeypatch):
    monkeypatch.setattr(scheduler_config, "SessionLocal", lambda: db_session)

    schedule = scheduler_config.build_beat_schedule(scheduler_timezone="Africa/Lagos")

    assert schedule["weekly_reporting"]["task"] == "app.tasks.reports.run_weekly_inbound_reporting"
    weekly_schedule = schedule["weekly_reporting"]["schedule"]
    assert weekly_schedule.hour == {8}
    _assert_due(
        weekly_schedule,
        scheduler_timezone="Africa/Lagos",
        now=datetime(2026, 7, 20, 8, 0, tzinfo=ZoneInfo("Africa/Lagos")),
        last_run_at=datetime(2026, 7, 13, 8, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    )
