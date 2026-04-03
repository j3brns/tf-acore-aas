from __future__ import annotations

from src.bridge.utils import get_retry_jitter


def should_retry(*, attempt: int, max_retry_attempts: int) -> bool:
    return attempt <= max_retry_attempts


def retry_delay_seconds(*, attempt: int) -> int:
    base_delay = min(900, 2**attempt)
    jittered_delay = int(get_retry_jitter(float(base_delay)))
    if base_delay > 0:
        return max(1, min(900, jittered_delay))
    return 0
