from __future__ import annotations

from datetime import datetime
from typing import Any

from src.platform_utils import (
    coerce_optional_string as _coerce_optional_string,
)
from src.platform_utils import (
    coerce_positive_int as _coerce_positive_int,
)
from src.platform_utils import (
    get_retry_jitter as _get_retry_jitter,
)
from src.platform_utils import (
    iso_utc as _iso_utc,
)
from src.platform_utils import (
    json_default as _json_default,
)
from src.platform_utils import (
    now_utc as _now_utc,
)

_OVERRIDE_NOW: datetime | None = None


def now_utc() -> datetime:
    if _OVERRIDE_NOW:
        return _OVERRIDE_NOW
    return _now_utc()


def get_retry_jitter(base_delay: float) -> float:
    return _get_retry_jitter(base_delay)


def iso(dt: datetime) -> str:
    return _iso_utc(dt)


def str_or_none(value: Any) -> str | None:
    return _coerce_optional_string(value)


def json_default(value: Any) -> Any:
    return _json_default(value)


def as_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number") from None


def coerce_positive_int(value: Any, *, default: int) -> int:
    return _coerce_positive_int(value, default=default)
