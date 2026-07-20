"""Automated Weekly Sales and Support inbound reporting."""

from app.services.weekly_reporting.configuration import (
    WeeklyReportingConfig,
    add_recipient,
    get_settings_snapshot,
    load_configuration,
    remove_recipient,
    save_schedule,
    update_recipient,
)
from app.services.weekly_reporting.engine import run_weekly_reporting

__all__ = [
    "WeeklyReportingConfig",
    "add_recipient",
    "get_settings_snapshot",
    "load_configuration",
    "remove_recipient",
    "run_weekly_reporting",
    "save_schedule",
    "update_recipient",
]
