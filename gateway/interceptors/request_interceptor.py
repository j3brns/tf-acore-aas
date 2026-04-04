"""
gateway.interceptors.request_interceptor — AgentCore Gateway REQUEST interceptor.

On every tool invocation:
  1. Validates Bearer JWT against Entra JWKS
  2. Checks tierMinimum for the requested tool — returns 403 if insufficient
  3. Issues scoped act-on-behalf token (5-minute TTL) for the specific tool
  4. Injects x-tenant-id, x-app-id, x-tier, x-acting-sub headers
  5. Enforces idempotency keyed on Mcp-Session-Id + body.id

The original user JWT never reaches a tool Lambda (see ADR-004).

Implemented in TASK-036.
ADRs: ADR-004
"""

import hashlib
import os
import time
from collections.abc import Callable
from typing import Any

import jwt
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.idempotency import (
    DynamoDBPersistenceLayer,
    IdempotencyConfig,
    idempotent_function,
)
from aws_lambda_powertools.utilities.parameters import get_secret
from jwt import PyJWKClient

from gateway.interceptors import request_idempotency, request_tier, request_token
from src.platform_utils import coerce_positive_int, parse_json_object_or_empty

try:
    from data_access import ControlPlaneDynamoDB, TenantCapabilityClient, TenantContext, TenantTier
except ImportError:
    # Fallback for environments where data-access-lib is not yet bundled
    TenantCapabilityClient = None
    ControlPlaneDynamoDB = None
    TenantContext = None
    TenantTier = None

logger = Logger(service="gateway-request-interceptor")
tracer = Tracer()

ENTRA_JWKS_URL = os.environ.get("ENTRA_JWKS_URL")
ENTRA_AUDIENCE = os.environ.get("ENTRA_AUDIENCE")
ENTRA_ISSUER = os.environ.get("ENTRA_ISSUER")
TOOLS_TABLE = os.environ.get("TOOLS_TABLE", "platform-tools")
SCOPED_TOKEN_ISSUER = os.environ.get("SCOPED_TOKEN_ISSUER", "platform-gateway")

_TIER_ORDER = {"basic": 0, "standard": 1, "premium": 2}
_jwk_client: PyJWKClient | None = None
_capability_client: Any | None = None


def get_capability_client():
    """Lazy initialization of TenantCapabilityClient."""
    global _capability_client
    if _capability_client is None and TenantCapabilityClient:
        _capability_client = TenantCapabilityClient()
    return _capability_client


def get_capability_policy():
    if not (
        os.environ.get("APPCONFIG_APPLICATION_ID")
        and os.environ.get("APPCONFIG_ENVIRONMENT_ID")
        and os.environ.get("APPCONFIG_PROFILE_ID")
    ):
        return None
    capability_client = get_capability_client()
    if not capability_client:
        return None
    return capability_client.fetch_policy()


_warned_fallback_signing_key = False
_idempotency_handler: Callable[..., dict[str, Any]] | None = None
_idempotency_handler_table: str | None = None

_scoped_token_signing_key_cache: str | None = None
_scoped_token_signing_key_expiry: float = 0


def _scoped_token_ttl_seconds() -> int:
    raw_value = os.environ.get("SCOPED_TOKEN_TTL_SECONDS")
    if raw_value is None:
        return 300
    parsed = coerce_positive_int(raw_value, default=300)
    if str(parsed) != str(raw_value).strip():
        logger.warning(
            "Invalid SCOPED_TOKEN_TTL_SECONDS, falling back to 300",
            extra={"value": raw_value},
        )
        return 300
    return parsed


def get_jwk_client() -> PyJWKClient | None:
    """Lazily initialize JWKS client with 5-minute cache."""
    global _jwk_client
    if _jwk_client is None and ENTRA_JWKS_URL:
        _jwk_client = PyJWKClient(ENTRA_JWKS_URL, cache_jwk_set=True, lifespan=300)
    return _jwk_client


def get_platform_context():
    if TenantContext is None or TenantTier is None:
        raise RuntimeError("data-access-lib is required for tool record lookups")
    return TenantContext(
        tenant_id="platform",
        app_id="gateway-request-interceptor",
        tier=TenantTier.PREMIUM,
        sub="request-interceptor",
    )


def _get_header(headers: dict[str, str], key: str) -> str | None:
    key_lower = key.lower()
    for header_key, value in headers.items():
        if header_key.lower() == key_lower:
            return value
    return None


def _normalized_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    output: dict[str, str] = {}
    for key, value in headers.items():
        output[str(key)] = str(value)
    return output


def _parse_body(body: Any) -> dict[str, Any]:
    return parse_json_object_or_empty(body)


def _build_interceptor_response(
    *,
    transformed_gateway_request: dict[str, Any],
    transformed_gateway_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mcp: dict[str, Any] = {"transformedGatewayRequest": transformed_gateway_request}
    if transformed_gateway_response is not None:
        mcp["transformedGatewayResponse"] = transformed_gateway_response
    return {"interceptorOutputVersion": "1.0", "mcp": mcp}


def _error_response(
    *,
    gateway_request: dict[str, Any],
    request_id: Any,
    status_code: int,
    code: int,
    message: str,
) -> dict[str, Any]:
    transformed_request = {
        "body": _parse_body(gateway_request.get("body")),
        "headers": _normalized_headers(gateway_request.get("headers", {})),
    }
    transformed_gateway_response = {
        "statusCode": status_code,
        "body": {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
    }
    return _build_interceptor_response(
        transformed_gateway_request=transformed_request,
        transformed_gateway_response=transformed_gateway_response,
    )


def _extract_tool_name(method: str, body: dict[str, Any]) -> str | None:
    if method != "tools/call":
        return None
    params = body.get("params", {})
    if not isinstance(params, dict):
        return None
    name = params.get("name") or params.get("toolName")
    if not name:
        return None
    return str(name)


def _build_idempotency_key(headers: dict[str, str], body: dict[str, Any]) -> str | None:
    return request_idempotency.build_idempotency_key(headers, body, get_header=_get_header)


def _is_tier_allowed(tenant_tier: str, minimum_tier: str) -> bool:
    return request_tier.is_tier_allowed(tenant_tier, minimum_tier, tier_order=_TIER_ORDER)


def _extract_minimum_tier(tool_record: dict[str, Any]) -> str:
    return request_tier.extract_minimum_tier(tool_record)


def get_tool_record(tool_name: str, tenant_id: str) -> dict[str, Any] | None:
    if ControlPlaneDynamoDB is None:
        raise RuntimeError("data-access-lib is required for tool record lookups")
    return request_tier.get_tool_record(
        tool_name,
        tenant_id,
        db_factory=ControlPlaneDynamoDB,
        get_platform_context=get_platform_context,
        tools_table=TOOLS_TABLE,
    )


def _validate_bearer_token(token: str) -> dict[str, Any] | None:
    return request_token.validate_bearer_token(
        token,
        entra_audience=ENTRA_AUDIENCE,
        entra_issuer=ENTRA_ISSUER,
        get_jwk_client=get_jwk_client,
        jwt_module=jwt,
        logger=logger,
    )


def _get_scoped_token_signing_key() -> str:
    global _warned_fallback_signing_key
    global _scoped_token_signing_key_cache
    global _scoped_token_signing_key_expiry

    platform_env = os.environ.get("PLATFORM_ENV", "prod")

    # 1. Check for explicit environment variable (Local/Tests precedence)
    explicit = os.environ.get("SCOPED_TOKEN_SIGNING_KEY")
    if explicit and platform_env == "local":
        if len(explicit) < 32:
            logger.warning("SCOPED_TOKEN_SIGNING_KEY is too short (min 32 bytes recommended)")
        return explicit

    # 2. Check for Secret ARN (Production standard)
    secret_arn = os.environ.get("SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN")
    if secret_arn:
        now = time.time()
        if _scoped_token_signing_key_cache and now < _scoped_token_signing_key_expiry:
            return _scoped_token_signing_key_cache

        try:
            # 5-minute cache in parameters utility (plus our own local cache)
            val = get_secret(secret_arn, max_age=300)
            if val and isinstance(val, str):
                _scoped_token_signing_key_cache = val
                _scoped_token_signing_key_expiry = now + 300
                return val
        except Exception:
            logger.exception("Failed to fetch scoped token signing key from Secrets Manager")
            # Fall through if we can't get the secret

    # 3. Deterministic fallback for local/dev ONLY.
    if platform_env != "local":
        raise RuntimeError(
            "SCOPED_TOKEN_SIGNING_KEY or SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN "
            "must be configured in production"
        )
    # Local fallback (deprecated, will be removed once all environments are seeded)
    seed = "|".join(
        [
            os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "gateway-request-interceptor"),
            os.environ.get("AWS_REGION", ""),
            SCOPED_TOKEN_ISSUER,
        ]
    )
    if not _warned_fallback_signing_key:
        logger.warning(
            "Using fallback scoped token signing key; configure SCOPED_TOKEN_SIGNING_KEY"
        )
        _warned_fallback_signing_key = True
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _issue_scoped_token(
    *,
    tenant_id: str,
    app_id: str,
    tier: str,
    acting_sub: str,
    scope_tool: str,
    mcp_session_id: str | None,
    mcp_request_id: Any,
) -> str:
    return request_token.issue_scoped_token(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=tier,
        acting_sub=acting_sub,
        scope_tool=scope_tool,
        mcp_session_id=mcp_session_id,
        mcp_request_id=mcp_request_id,
        scoped_token_issuer=SCOPED_TOKEN_ISSUER,
        ttl_seconds=_scoped_token_ttl_seconds(),
        signing_key=_get_scoped_token_signing_key(),
        jwt_module=jwt,
    )


def _get_idempotency_handler() -> Callable[..., dict[str, Any]] | None:
    """Create a Powertools idempotent wrapper when IDEMPOTENCY_TABLE is configured."""
    global _idempotency_handler, _idempotency_handler_table
    table_name = os.environ.get("IDEMPOTENCY_TABLE")
    if not table_name:
        return None

    if _idempotency_handler is not None and _idempotency_handler_table == table_name:
        return _idempotency_handler

    _idempotency_handler = request_idempotency.create_idempotency_handler(
        process_request=_process_request,
        table_name=table_name,
        expires_after_seconds=_scoped_token_ttl_seconds(),
        persistence_layer_cls=DynamoDBPersistenceLayer,
        config_cls=IdempotencyConfig,
        idempotent_decorator=idempotent_function,
    )
    _idempotency_handler_table = table_name
    return _idempotency_handler


def _process_request(event: dict[str, Any]) -> dict[str, Any]:
    mcp = event.get("mcp", {})
    if not isinstance(mcp, dict):
        mcp = {}
    gateway_request = mcp.get("gatewayRequest", {})
    if not isinstance(gateway_request, dict):
        gateway_request = {}

    request_body = _parse_body(gateway_request.get("body"))
    request_headers = _normalized_headers(gateway_request.get("headers", {}))
    jsonrpc_id = request_body.get("id")

    authorization = _get_header(request_headers, "Authorization")
    if not authorization or not authorization.startswith("Bearer "):
        return _error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Missing or invalid Bearer token",
        )

    user_token = authorization.split(" ", 1)[1]
    try:
        payload = _validate_bearer_token(user_token)
    except jwt.ExpiredSignatureError:
        return _error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token expired",
        )
    except jwt.InvalidTokenError:
        return _error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token validation failed",
        )
    except Exception:
        logger.exception("Unexpected JWT validation error")
        return _error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Bearer token validation failed",
        )

    if payload is None:
        return _error_response(
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
        return _error_response(
            gateway_request=gateway_request,
            request_id=jsonrpc_id,
            status_code=401,
            code=-32001,
            message="Missing tenant context in token",
        )

    logger.append_keys(tenant_id=tenant_id, app_id=app_id)

    method = str(request_body.get("method") or "")
    tool_name, tool_error = request_tier.validate_tool_access(
        method=method,
        request_body=request_body,
        gateway_request=gateway_request,
        request_id=jsonrpc_id,
        tenant_id=tenant_id,
        tier=tier,
        extract_tool_name=_extract_tool_name,
        get_tool_record=get_tool_record,
        get_capability_policy=get_capability_policy,
        extract_minimum_tier=_extract_minimum_tier,
        is_tier_allowed=_is_tier_allowed,
        error_response=_error_response,
        logger=logger,
    )
    if tool_error is not None:
        return tool_error

    scope_tool = tool_name if tool_name else method
    scoped_token = _issue_scoped_token(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=tier,
        acting_sub=acting_sub,
        scope_tool=scope_tool,
        mcp_session_id=_get_header(request_headers, "Mcp-Session-Id"),
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

    return _build_interceptor_response(transformed_gateway_request=transformed_request)


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AgentCore Gateway REQUEST interceptor entrypoint."""
    mcp = event.get("mcp", {})
    if not isinstance(mcp, dict):
        return _error_response(
            gateway_request={},
            request_id=None,
            status_code=400,
            code=-32600,
            message="Invalid interceptor input",
        )

    gateway_request = mcp.get("gatewayRequest", {})
    if not isinstance(gateway_request, dict):
        return _error_response(
            gateway_request={},
            request_id=None,
            status_code=400,
            code=-32600,
            message="Invalid gateway request",
        )

    request_body = _parse_body(gateway_request.get("body"))
    request_headers = _normalized_headers(gateway_request.get("headers", {}))
    idempotency_key = _build_idempotency_key(request_headers, request_body)
    idempotency_handler = _get_idempotency_handler()

    if idempotency_handler and idempotency_key:
        return idempotency_handler(
            idempotency_data={"idempotency_key": idempotency_key},
            interceptor_event=event,
        )

    return _process_request(event)
