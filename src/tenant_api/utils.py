from __future__ import annotations

import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

_OVERRIDE_NOW: datetime | None = None


def now_utc() -> datetime:
    if _OVERRIDE_NOW:
        return _OVERRIDE_NOW
    return datetime.now(UTC)


def get_retry_jitter(base_delay: float) -> float:
    """Return a random jittered delay between 0 and base_delay using Full Jitter strategy."""
    return secrets.SystemRandom().uniform(0, base_delay)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def as_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number") from None


def coerce_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(1, parsed)
