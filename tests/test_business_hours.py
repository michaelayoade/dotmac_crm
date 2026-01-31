"""Tests for business hours computation."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.schemas.crm.chat_widget import BusinessHours, BusinessHoursDay
from app.services.crm.chat_widget import is_within_business_hours


class TestIsWithinBusinessHours:
    """Tests for the is_within_business_hours function."""

    def test_none_business_hours_returns_online(self):
        """No business hours configured means always online."""
        assert is_within_business_hours(None) is True

    def test_within_hours_returns_online(self):
        """Returns True when within configured business hours."""
        # Monday at 10:00 AM UTC
        mock_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh = BusinessHours(
            timezone="UTC",
            monday=BusinessHoursDay(enabled=True, start="09:00", end="17:00"),
        )

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            assert is_within_business_hours(bh) is True

    def test_outside_hours_returns_offline(self):
        """Returns False when outside configured business hours."""
        # Monday at 6:00 AM UTC (before 9 AM)
        mock_time = datetime(2024, 1, 15, 6, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh = BusinessHours(
            timezone="UTC",
            monday=BusinessHoursDay(enabled=True, start="09:00", end="17:00"),
        )

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            assert is_within_business_hours(bh) is False

    def test_after_hours_returns_offline(self):
        """Returns False when after configured business hours."""
        # Monday at 6:00 PM UTC (after 5 PM)
        mock_time = datetime(2024, 1, 15, 18, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh = BusinessHours(
            timezone="UTC",
            monday=BusinessHoursDay(enabled=True, start="09:00", end="17:00"),
        )

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            assert is_within_business_hours(bh) is False

    def test_disabled_day_returns_offline(self):
        """Returns False when the day is disabled."""
        # Saturday at 10:00 AM UTC
        mock_time = datetime(2024, 1, 13, 10, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh = BusinessHours(
            timezone="UTC",
            saturday=BusinessHoursDay(enabled=False),
        )

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            assert is_within_business_hours(bh) is False

    def test_invalid_timezone_returns_online(self):
        """Invalid timezone fails safe to online."""
        bh = BusinessHours(timezone="Invalid/Timezone")

        # Should return True (fail-safe) without raising
        assert is_within_business_hours(bh) is True

    def test_overnight_hours_before_midnight(self):
        """Handles overnight hours (e.g., 22:00 - 06:00) - check before midnight."""
        # Monday at 23:00 UTC
        mock_time = datetime(2024, 1, 15, 23, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh = BusinessHours(
            timezone="UTC",
            monday=BusinessHoursDay(enabled=True, start="22:00", end="06:00"),
        )

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            assert is_within_business_hours(bh) is True

    def test_overnight_hours_after_midnight(self):
        """Handles overnight hours (e.g., 22:00 - 06:00) - check after midnight."""
        # Tuesday at 03:00 UTC (still within Monday's overnight shift)
        mock_time = datetime(2024, 1, 16, 3, 0, 0, tzinfo=ZoneInfo("UTC"))

        # Note: This is Tuesday, so we check Tuesday's config for the overnight
        bh = BusinessHours(
            timezone="UTC",
            tuesday=BusinessHoursDay(enabled=True, start="22:00", end="06:00"),
        )

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            assert is_within_business_hours(bh) is True

    def test_overnight_hours_gap(self):
        """Outside overnight hours returns offline."""
        # Monday at 12:00 UTC (between 06:00 and 22:00 - not in shift)
        mock_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh = BusinessHours(
            timezone="UTC",
            monday=BusinessHoursDay(enabled=True, start="22:00", end="06:00"),
        )

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            assert is_within_business_hours(bh) is False

    def test_timezone_conversion(self):
        """Correctly handles timezone conversion."""
        # UTC time is 14:00, but in America/New_York it's 9:00 or 10:00 depending on DST
        # Using January (no DST), so UTC-5 means 14:00 UTC = 09:00 EST
        mock_time = datetime(2024, 1, 15, 14, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh = BusinessHours(
            timezone="America/New_York",
            monday=BusinessHoursDay(enabled=True, start="09:00", end="17:00"),
        )

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            # The function calls datetime.now(tz), so mock it to convert properly
            mock_datetime.now.return_value = mock_time.astimezone(ZoneInfo("America/New_York"))
            assert is_within_business_hours(bh) is True

    def test_dict_input(self):
        """Accepts dict input (from JSON storage)."""
        # Monday at 10:00 AM UTC
        mock_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh_dict = {
            "timezone": "UTC",
            "monday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "tuesday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "wednesday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "thursday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "friday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "saturday": {"enabled": False, "start": "09:00", "end": "17:00"},
            "sunday": {"enabled": False, "start": "09:00", "end": "17:00"},
        }

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            assert is_within_business_hours(bh_dict) is True

    def test_at_start_time_returns_online(self):
        """Returns True when exactly at start time."""
        # Monday at 09:00 AM UTC
        mock_time = datetime(2024, 1, 15, 9, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh = BusinessHours(
            timezone="UTC",
            monday=BusinessHoursDay(enabled=True, start="09:00", end="17:00"),
        )

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            assert is_within_business_hours(bh) is True

    def test_at_end_time_returns_offline(self):
        """Returns False when exactly at end time (end time is exclusive)."""
        # Monday at 17:00 UTC
        mock_time = datetime(2024, 1, 15, 17, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh = BusinessHours(
            timezone="UTC",
            monday=BusinessHoursDay(enabled=True, start="09:00", end="17:00"),
        )

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            assert is_within_business_hours(bh) is False

    def test_malformed_time_fails_safe(self):
        """Malformed time string fails safe to online."""
        # Monday at 10:00 AM UTC
        mock_time = datetime(2024, 1, 15, 10, 0, 0, tzinfo=ZoneInfo("UTC"))

        bh_dict = {
            "timezone": "UTC",
            "monday": {"enabled": True, "start": "invalid", "end": "17:00"},
            "tuesday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "wednesday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "thursday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "friday": {"enabled": True, "start": "09:00", "end": "17:00"},
            "saturday": {"enabled": False, "start": "09:00", "end": "17:00"},
            "sunday": {"enabled": False, "start": "09:00", "end": "17:00"},
        }

        with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_time
            # Should fail-safe to True
            assert is_within_business_hours(bh_dict) is True

    def test_all_days(self):
        """Test each day of the week."""
        # Test days 0-6 (Mon-Sun)
        day_dates = {
            0: datetime(2024, 1, 15, 10, 0, 0, tzinfo=ZoneInfo("UTC")),  # Monday
            1: datetime(2024, 1, 16, 10, 0, 0, tzinfo=ZoneInfo("UTC")),  # Tuesday
            2: datetime(2024, 1, 17, 10, 0, 0, tzinfo=ZoneInfo("UTC")),  # Wednesday
            3: datetime(2024, 1, 18, 10, 0, 0, tzinfo=ZoneInfo("UTC")),  # Thursday
            4: datetime(2024, 1, 19, 10, 0, 0, tzinfo=ZoneInfo("UTC")),  # Friday
            5: datetime(2024, 1, 20, 10, 0, 0, tzinfo=ZoneInfo("UTC")),  # Saturday
            6: datetime(2024, 1, 21, 10, 0, 0, tzinfo=ZoneInfo("UTC")),  # Sunday
        }

        # Default BusinessHours has Mon-Fri enabled, Sat-Sun disabled
        bh = BusinessHours(timezone="UTC")

        for day_num, mock_time in day_dates.items():
            with patch("app.services.crm.chat_widget.datetime") as mock_datetime:
                mock_datetime.now.return_value = mock_time
                result = is_within_business_hours(bh)

                # Mon-Fri (0-4) should be online, Sat-Sun (5-6) offline
                if day_num < 5:
                    assert result is True, f"Day {day_num} should be online"
                else:
                    assert result is False, f"Day {day_num} should be offline"
