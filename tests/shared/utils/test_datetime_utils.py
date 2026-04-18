"""Unit tests for shared datetime helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from freezegun import freeze_time

from src.shared.utils.datetime_utils import (
    add_days,
    add_hours,
    add_minutes,
    format_datetime,
    get_time_until,
    get_timestamp,
    get_utc_now,
    is_expired,
)


class TestUtcNow:
    @freeze_time("2026-04-18T12:00:00+00:00")
    def test_returns_aware_utc(self) -> None:
        now = get_utc_now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)
        assert now == datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)


class TestGetTimestamp:
    @freeze_time("2026-04-18T12:00:00+00:00")
    def test_iso8601_format(self) -> None:
        assert get_timestamp().startswith("2026-04-18T12:00:00")
        assert get_timestamp().endswith("+00:00")


class TestIsExpired:
    @freeze_time("2026-04-18T12:00:00+00:00")
    def test_past_time_is_expired(self) -> None:
        past = datetime(2026, 4, 18, 11, 0, tzinfo=UTC)
        assert is_expired(past) is True

    @freeze_time("2026-04-18T12:00:00+00:00")
    def test_future_time_not_expired(self) -> None:
        future = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
        assert is_expired(future) is False


class TestAdders:
    @freeze_time("2026-04-18T12:00:00+00:00")
    def test_add_hours_default_now(self) -> None:
        assert add_hours(hours=2) == datetime(2026, 4, 18, 14, 0, tzinfo=UTC)

    def test_add_hours_explicit_dt(self) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        assert add_hours(base, hours=24) == datetime(2026, 1, 2, tzinfo=UTC)

    @freeze_time("2026-04-18T12:00:00+00:00")
    def test_add_days_default_now(self) -> None:
        assert add_days(days=3).day == 21

    @freeze_time("2026-04-18T12:00:00+00:00")
    def test_add_minutes_default_now(self) -> None:
        assert add_minutes(minutes=15).minute == 15

    def test_add_minutes_explicit_dt(self) -> None:
        base = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
        assert add_minutes(base, minutes=45).hour == 9
        assert add_minutes(base, minutes=45).minute == 45


class TestFormatDatetime:
    def test_with_time(self) -> None:
        dt = datetime(2026, 4, 18, 9, 5, 7, tzinfo=UTC)
        assert format_datetime(dt) == "2026-04-18 09:05:07"

    def test_date_only(self) -> None:
        dt = datetime(2026, 4, 18, tzinfo=UTC)
        assert format_datetime(dt, include_time=False) == "2026-04-18"


class TestGetTimeUntil:
    @freeze_time("2026-04-18T12:00:00+00:00")
    def test_target_in_past_returns_zeros(self) -> None:
        past = datetime(2026, 4, 17, tzinfo=UTC)
        assert get_time_until(past) == {"days": 0, "hours": 0, "minutes": 0, "seconds": 0}

    @freeze_time("2026-04-18T12:00:00+00:00")
    def test_breakdown_correct(self) -> None:
        target = datetime(2026, 4, 19, 15, 30, 45, tzinfo=UTC)
        result = get_time_until(target)
        assert result == {"days": 1, "hours": 3, "minutes": 30, "seconds": 45}

    @freeze_time("2026-04-18T12:00:00+00:00")
    def test_target_now_returns_zeros(self) -> None:
        now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)
        assert get_time_until(now) == {"days": 0, "hours": 0, "minutes": 0, "seconds": 0}
