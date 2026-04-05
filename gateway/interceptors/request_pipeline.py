from __future__ import annotations

from typing import Any


def process_request(
    event: dict[str, Any],
    *,
    parse_body: Any,
    normalized_headers: Any,
    get_header: Any,
    error_response: Any,
    validate_bearer_token: Any,
    validate_tool_access: Any,
    issue_scoped_token: Any,
    logger: Any,
    jwt_module: Any,
) -> dict[str, Any]:
    mcp = event.get("mcp", {})
    if not isinstance(mcp, dict):
        mcp = {}
    gateway_request = mcp.get("gatewayRequest", {})
    if not isinstance(gateway_request, dict):
        gateway_request = {}
    request_body = parse_body(gateway_request.get("body"))
    request_headers = normalized_headers(gateway_request.get("headers", {}))
    jsonrpc_id = request_body.get("id")
    authorization = get_header(request_headers, "Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Missing or invalid Bearer token",
        )
    user_token = authorization.split(" ", 1)[1]
    try:
        payload = validate_bearer_token(user_token)
    except jwt_module.ExpiredSignatureError:
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token expired",
        )
    except jwt_module.InvalidTokenError:
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token validation failed",
        )
    except Exception:
        logger.exception("Unexpected JWT validation error")
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token validation failed",
        )
    if payload is None:
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token validation failed",
        )
    tenant_id = str(payload.get("tenantid") or "")
    app_id = str(payload.get("appid") or "")
    tier = str(payload.get("tier") or "basic")
    acting_sub = str(payload.get("sub") or "unknown")
    if not tenant_id or not app_id:
        return error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Missing tenant context in token",
        )
    logger.append_keys(tenant_id=tenant_id, app_id=app_id)
    method = str(request_body.get("method") or "")
    tool_name, tool_error = validate_tool_access(
        method=method,
        request_body=request_body,
        gateway_request=gateway_request,
        request_id=jsonrpc_id,
        tenant_id=tenant_id,
        tier=tier,
    )
    if tool_error is not None:
        return tool_error
    scope_tool = tool_name if tool_name else method
    scoped_token = issue_scoped_token(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=tier,
        acting_sub=acting_sub,
        scope_tool=scope_tool,
        mcp_session_id=get_header(request_headers, "Mcp-Session-Id"),
        mcp_request_id=jsonrpc_id,
    )
    transformed_headers = {
        key: value for key, value in request_headers.items() if key.lower() != "authorization"
    }
    transformed_headers["Authorization"] = f"Bearer {scoped_token}"
    transformed_headers["x-tenant-id"] = tenant_id
    transformed_headers["x-app-id"] = app_id
    transformed_headers["x-tier"] = tier
    transformed_headers["x-acting-sub"] = acting_sub
    transformed_request = dict(gateway_request)
    transformed_request["headers"] = transformed_headers
    transformed_request["body"] = request_body
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": transformed_request},
    }
