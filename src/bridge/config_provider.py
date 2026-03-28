from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class ConfigProvider:
    """Cache-backed configuration provider for Bridge runtime settings."""

    def __init__(
        self,
        *,
        fetcher: Callable[[], dict[str, Any]],
        fallback_factory: Callable[[], dict[str, Any]],
        ttl_seconds: int = 60,
    ) -> None:
        self._fetcher = fetcher
        self._fallback_factory = fallback_factory
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, Any] = {}
        self._expires_at = 0.0

    @property
    def expires_at(self) -> float:
        return self._expires_at

    def get(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force_refresh and self._cache and now < self._expires_at:
            return self._cache

        try:
            self._cache = self._fetcher()
        except Exception:
            if not self._cache:
                self._cache = self._fallback_factory()
        self._expires_at = now + self._ttl_seconds
        return self._cache
