from __future__ import annotations

from datetime import datetime
from typing import Any

from src.platform_utils import (
    coerce_optional_int as _coerce_optional_int,
)
from src.platform_utils import (
    coerce_optional_string as _coerce_optional_string,
)
from src.platform_utils import (
    get_retry_jitter as _get_retry_jitter,
)
from src.platform_utils import (
    iso_utc as _iso_utc,
)
from src.platform_utils import (
    now_utc as _now_utc,
)


def now_utc() -> datetime:
    return _now_utc()


def iso(ts: datetime) -> str:
    return _iso_utc(ts)


def get_retry_jitter(base_delay: float) -> float:
    return _get_retry_jitter(base_delay)


def coerce_optional_string(val: Any) -> str | None:
    return _coerce_optional_string(val)


def coerce_optional_int(val: Any) -> int | None:
    return _coerce_optional_int(val)
