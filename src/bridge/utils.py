from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(ts: datetime) -> str:
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_retry_jitter(base_delay: float) -> float:
    """Return a random jittered delay between 0 and base_delay using Full Jitter strategy."""
    return secrets.SystemRandom().uniform(0, base_delay)


def coerce_optional_string(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def coerce_optional_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
