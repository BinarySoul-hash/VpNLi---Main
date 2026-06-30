"""
Safe date parsing utilities for the VPN bot.
Centralizes all expires_at / datetime handling to prevent crashes on bad data.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def safe_parse_expires_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def parse_expires_at_or_now(value: str | None) -> datetime:
    return safe_parse_expires_at(value) or _utcnow()


def remaining_seconds(expires_at: datetime) -> float:
    now = _utcnow()
    return max(0.0, (expires_at - now).total_seconds())


def remaining_days(expires_at: datetime) -> int:
    return int((remaining_seconds(expires_at) + 86399) // 86400)


def fmt_date(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y")


def fmt_datetime(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y %H:%M")


_EPOCH = datetime(1970, 1, 1)


def expires_dt_to_ms(dt: datetime) -> int:
    """Convert a naive UTC datetime to XUI milliseconds timestamp.

    Unlike dt.timestamp() which assumes local time for naive datetimes,
    this always treats the input as UTC.
    """
    return int((dt - _EPOCH).total_seconds() * 1000)
