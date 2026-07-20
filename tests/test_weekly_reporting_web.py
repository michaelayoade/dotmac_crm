from __future__ import annotations

from app.models.domain_settings import SettingDomain
from app.services import scheduler_config
from app.services.weekly_reporting import configuration
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


def test_weekly_reporting_schedule_defaults_to_monday_0800_lagos(db_session):
    schedule = scheduler_config._weekly_reporting_crontab(db_session)

    assert schedule.minute == {0}
    assert schedule.hour == {7}
    assert schedule.day_of_week == {1}


def test_weekly_reporting_schedule_uses_notification_configuration(db_session):
    configuration.save_schedule(
        db_session,
        enabled=True,
        schedule_day="wednesday",
        schedule_time="09:30",
        timezone="Africa/Lagos",
    )

    schedule = scheduler_config._weekly_reporting_crontab(db_session)

    assert schedule.minute == {30}
    assert schedule.hour == {8}
    assert schedule.day_of_week == {3}
    specs = {spec.key for spec in system_web._generic_settings_specs(SettingDomain.notification)}
    assert configuration.CUSTOM_SETTING_KEYS.isdisjoint(specs)


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

    schedule = scheduler_config._weekly_reporting_crontab(db_session)

    assert schedule.minute == {30}
    assert schedule.hour == {8}
    assert schedule.day_of_week == {3}


def test_weekly_reporting_task_is_registered_with_existing_celery_beat(db_session, monkeypatch):
    monkeypatch.setattr(scheduler_config, "SessionLocal", lambda: db_session)

    schedule = scheduler_config.build_beat_schedule()

    assert schedule["weekly_reporting"]["task"] == "app.tasks.reports.run_weekly_inbound_reporting"
    assert schedule["weekly_reporting"]["schedule"].hour == {7}
