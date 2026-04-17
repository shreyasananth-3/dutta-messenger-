"""Date and time utilities for DuttaMessenger.

Provides helpers for working with timezone-aware datetimes
and timestamp formatting.
"""

from datetime import datetime, timedelta, timezone

import structlog

logger = structlog.get_logger()


def get_utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime.

    Returns:
        Current UTC datetime with timezone info.

    Example:
        now = get_utc_now()
        # Returns: datetime.datetime(2024, 1, 15, 10, 30, 45, tzinfo=datetime.timezone.utc)
    """
    return datetime.now(timezone.utc)


def get_timestamp() -> str:
    """Get current UTC timestamp as ISO 8601 string.

    Returns:
        Current timestamp in ISO 8601 format.

    Example:
        timestamp = get_timestamp()
        # Returns: "2024-01-15T10:30:45.123456+00:00"
    """
    return get_utc_now().isoformat()


def is_expired(expire_time: datetime) -> bool:
    """Check if a datetime has passed.

    Args:
        expire_time: Time to check against.

    Returns:
        True if expire_time is in the past, False otherwise.
    """
    return expire_time < get_utc_now()


def add_hours(dt: datetime | None = None, hours: int = 1) -> datetime:
    """Add hours to a datetime.

    Args:
        dt: Base datetime (default: now).
        hours: Number of hours to add.

    Returns:
        New datetime with hours added.

    Example:
        future = add_hours(hours=24)
        # Returns datetime 24 hours from now
    """
    if dt is None:
        dt = get_utc_now()
    return dt + timedelta(hours=hours)


def add_days(dt: datetime | None = None, days: int = 1) -> datetime:
    """Add days to a datetime.

    Args:
        dt: Base datetime (default: now).
        days: Number of days to add.

    Returns:
        New datetime with days added.
    """
    if dt is None:
        dt = get_utc_now()
    return dt + timedelta(days=days)


def add_minutes(dt: datetime | None = None, minutes: int = 1) -> datetime:
    """Add minutes to a datetime.

    Args:
        dt: Base datetime (default: now).
        minutes: Number of minutes to add.

    Returns:
        New datetime with minutes added.
    """
    if dt is None:
        dt = get_utc_now()
    return dt + timedelta(minutes=minutes)


def format_datetime(dt: datetime, include_time: bool = True) -> str:
    """Format datetime for human reading.

    Args:
        dt: Datetime to format.
        include_time: Whether to include time component.

    Returns:
        Formatted datetime string.

    Example:
        formatted = format_datetime(datetime.now())
        # Returns: "2024-01-15 10:30:45"
    """
    if include_time:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d")


def get_time_until(target_time: datetime) -> dict[str, int]:
    """Get time remaining until a target datetime.

    Args:
        target_time: Target datetime.

    Returns:
        Dictionary with days, hours, minutes, seconds remaining.

    Example:
        time_left = get_time_until(some_future_time)
        # Returns: {'days': 1, 'hours': 3, 'minutes': 45, 'seconds': 30}
    """
    now = get_utc_now()
    if target_time <= now:
        return {"days": 0, "hours": 0, "minutes": 0, "seconds": 0}

    diff = target_time - now
    days = diff.days
    seconds = diff.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    return {
        "days": days,
        "hours": hours,
        "minutes": minutes,
        "seconds": seconds,
    }
