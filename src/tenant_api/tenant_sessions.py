from __future__ import annotations

from typing import Any

try:
    from . import http_utils, models
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import http_utils, models


def handle_sessions(
    event: dict[str, Any],
    caller: models.CallerIdentity,
) -> dict[str, object]:
    _ = event
    _ = caller
    return http_utils.response(200, {"items": []})
