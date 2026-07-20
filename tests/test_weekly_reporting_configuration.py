from __future__ import annotations

import pytest

from app.services.weekly_reporting import configuration


def test_weekly_reporting_defaults_are_disabled_monday_lagos(db_session):
    snapshot = configuration.get_settings_snapshot(db_session)

    assert snapshot == {
        "enabled": False,
        "recipients": [],
        "recipient_count": 0,
        "schedule_day": "monday",
        "schedule_time": "08:00",
        "timezone": "Africa/Lagos",
        "weekday_options": configuration.WEEKDAY_OPTIONS,
    }


def test_weekly_reporting_schedule_and_recipient_crud(db_session):
    configuration.save_schedule(
        db_session,
        enabled=True,
        schedule_day="wednesday",
        schedule_time="09:30",
        timezone="Africa/Lagos",
    )
    configuration.add_recipient(db_session, "Reports.One@Example.com")
    configuration.add_recipient(db_session, "reports.two@example.com")
    configuration.add_recipient(db_session, "reports.three@example.com")

    snapshot = configuration.update_recipient(db_session, 1, "updated@example.com")
    assert snapshot["enabled"] is True
    assert snapshot["schedule_day"] == "wednesday"
    assert snapshot["schedule_time"] == "09:30"
    assert snapshot["recipients"] == [
        "reports.one@example.com",
        "updated@example.com",
        "reports.three@example.com",
    ]

    snapshot = configuration.remove_recipient(db_session, 0)
    assert snapshot["recipients"] == ["updated@example.com", "reports.three@example.com"]


def test_weekly_reporting_rejects_invalid_and_duplicate_recipients(db_session):
    with pytest.raises(ValueError, match="valid recipient"):
        configuration.add_recipient(db_session, "not-an-email")

    configuration.add_recipient(db_session, "reports@example.com")
    with pytest.raises(ValueError, match="already configured"):
        configuration.add_recipient(db_session, "REPORTS@example.com")


def test_weekly_reporting_rejects_invalid_schedule_values(db_session):
    with pytest.raises(ValueError, match="valid weekly reporting day"):
        configuration.save_schedule(
            db_session,
            enabled=True,
            schedule_day="someday",
            schedule_time="08:00",
            timezone="Africa/Lagos",
        )
    with pytest.raises(ValueError, match="HH:MM"):
        configuration.save_schedule(
            db_session,
            enabled=True,
            schedule_day="monday",
            schedule_time="25:00",
            timezone="Africa/Lagos",
        )
    with pytest.raises(ValueError, match="IANA timezone"):
        configuration.save_schedule(
            db_session,
            enabled=True,
            schedule_day="monday",
            schedule_time="08:00",
            timezone="Not/AZone",
        )
