from __future__ import annotations

import json
from typing import Any


def get_header(headers: dict[str, str], key: str) -> str | None:
    key_lower = key.lower()
    for header_key, value in headers.items():
        if header_key.lower() == key_lower:
            return value
    return None


def normalized_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    return {str(key): str(value) for key, value in headers.items()}


def parse_body(body: Any) -> dict[str, Any]:
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


def build_interceptor_response(
    *,
    transformed_gateway_request: dict[str, Any],
    transformed_gateway_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mcp: dict[str, Any] = {"transformedGatewayRequest": transformed_gateway_request}
    if transformed_gateway_response is not None:
        mcp["transformedGatewayResponse"] = transformed_gateway_response
    return {"interceptorOutputVersion": "1.0", "mcp": mcp}


def error_response(
    *,
    gateway_request: dict[str, Any],
    request_id: Any,
    status_code: int,
    code: int,
    message: str,
    parse_body: Any,
    normalized_headers: Any,
    build_interceptor_response: Any,
) -> dict[str, Any]:
    transformed_request = {
        "body": parse_body(gateway_request.get("body")),
        "headers": normalized_headers(gateway_request.get("headers", {})),
    }
    transformed_gateway_response = {
        "statusCode": status_code,
        "body": {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
    }
    return build_interceptor_response(
        transformed_gateway_request=transformed_request,
        transformed_gateway_response=transformed_gateway_response,
    )
