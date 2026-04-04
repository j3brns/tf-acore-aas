from __future__ import annotations

from typing import Any


def build_idempotency_key(
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    get_header: Any,
) -> str | None:
    session_id = get_header(headers, "Mcp-Session-Id")
    request_id = body.get("id")
    if not session_id or request_id is None:
        return None
    return f"{session_id}:{request_id}"


def create_idempotency_handler(
    *,
    process_request: Any,
    table_name: str,
    expires_after_seconds: int,
    persistence_layer_cls: Any,
    config_cls: Any,
    idempotent_decorator: Any,
) -> Any:
    config = config_cls(
        event_key_jmespath="idempotency_key",
        expires_after_seconds=expires_after_seconds,
        use_local_cache=True,
    )
    persistence = persistence_layer_cls(table_name=table_name)

    @idempotent_decorator(
        data_keyword_argument="idempotency_data",
        persistence_store=persistence,
        config=config,
    )
    def _wrapper(
        *,
        idempotency_data: dict[str, str],
        interceptor_event: dict[str, Any],
    ) -> dict[str, Any]:
        return process_request(interceptor_event)

    return _wrapper
