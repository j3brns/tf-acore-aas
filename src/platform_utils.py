from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso_utc(ts: datetime) -> str:
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_retry_jitter(base_delay: float) -> float:
    """Return a random jittered delay between 0 and base_delay using Full Jitter strategy."""
    return secrets.SystemRandom().uniform(0, base_delay)


def get_hex_jitter(num_bytes: int = 1) -> str:
    return secrets.token_hex(num_bytes)


def coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_positive_int(value: Any, *, default: int) -> int:
    parsed = coerce_optional_int(value)
    if parsed is None:
        return default
    return max(1, parsed)


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def parse_json_object_or_empty(body: Any) -> dict[str, Any]:
    if isinstance(body, dict):
        return dict(body)
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}
