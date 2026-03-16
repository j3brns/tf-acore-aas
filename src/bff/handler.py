"""
bff.handler — Thin Backend-for-Frontend Lambda.

Handles two concerns only:
  - POST /v1/bff/token-refresh: Entra on-behalf-of token exchange
  - POST /v1/bff/session-keepalive: ping AgentCore Runtime to prevent idle timeout

Does NOT handle agent invocations.

Implemented in TASK-038.
ADRs: ADR-011
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.parameters import get_secret
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="bff")
tracer = Tracer()

# Environment variables (passed from CDK in PlatformStack)
ENTRA_CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID")
ENTRA_CLIENT_SECRET = os.environ.get("ENTRA_CLIENT_SECRET")
ENTRA_TENANT_ID = os.environ.get("ENTRA_TENANT_ID")
ENTRA_TOKEN_ENDPOINT = os.environ.get("ENTRA_TOKEN_ENDPOINT")
ENTRA_AUDIENCE = os.environ.get("ENTRA_AUDIENCE")

# Secret ARNs (passed from CDK in PlatformStack)
ENTRA_CLIENT_ID_SECRET_ARN = os.environ.get("ENTRA_CLIENT_ID_SECRET_ARN")
ENTRA_CLIENT_SECRET_SECRET_ARN = os.environ.get("ENTRA_CLIENT_SECRET_SECRET_ARN")

# In-memory cache for secrets resolved from ARNs
_secrets_cache: dict[str, str] = {}
_secrets_expiry: dict[str, float] = {}
_CACHE_TTL = 300  # 5 minutes

RUNTIME_PING_URL = os.environ.get("RUNTIME_PING_URL") or os.environ.get("MOCK_RUNTIME_URL")
RUNTIME_KEEPALIVE_WINDOW_SECONDS = 15 * 60
RUNTIME_PING_TIMEOUT_SECONDS = 2.0
_ALLOWED_SCOPE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,127}$")


def _resolve_secret(secret_arn: str | None, env_value: str | None, env_name: str) -> str:
    """Resolve a secret value from ARN (with 5-min cache) or environment variable fallback."""
    if not secret_arn:
        return _required_env_value(env_name, env_value)

    now = time.time()
    if secret_arn in _secrets_cache and now < _secrets_expiry.get(secret_arn, 0):
        return _secrets_cache[secret_arn]

    try:
        # get_secret includes its own 5-minute cache by default (max_age=300)
        val = get_secret(secret_arn, max_age=_CACHE_TTL)
        if val and isinstance(val, str):
            _secrets_cache[secret_arn] = val
            _secrets_expiry[secret_arn] = now + _CACHE_TTL
            return val
    except Exception:
        logger.exception(f"Failed to fetch {env_name} from Secrets Manager ARN: {secret_arn}")

    # Fallback to direct environment variable if secret fetch fails
    return _required_env_value(env_name, env_value)


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Route BFF requests to token refresh or session keepalive handlers."""
    request_id = _request_id(event, context)
    tenant_id, app_id = _tenant_and_app(event)
    if not tenant_id or not app_id:
        logger.warning("Missing tenant context in authorizer")
        return _error_response(401, "UNAUTHENTICATED", "Missing tenant context", request_id)

    logger.append_keys(tenantid=tenant_id, appid=app_id)

    method = _http_method(event)
    path = _path(event)

    if method != "POST":
        return _error_response(404, "NOT_FOUND", "Route not found", request_id)

    if path.endswith("/v1/bff/token-refresh"):
        return _handle_token_refresh(event, request_id=request_id)

    if path.endswith("/v1/bff/session-keepalive"):
        return _handle_session_keepalive(
            event,
            tenant_id=tenant_id,
            app_id=app_id,
            request_id=request_id,
        )

    return _error_response(404, "NOT_FOUND", "Route not found", request_id)


def _handle_token_refresh(event: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    access_token = _bearer_token(event)
    if access_token is None:
        return _error_response(401, "UNAUTHENTICATED", "Missing Authorization header", request_id)

    try:
        body = _require_json_body(event)
    except ValueError as exc:
        return _error_response(400, "INVALID_REQUEST", str(exc), request_id)

    raw_scopes = body.get("scopes")
    if not isinstance(raw_scopes, list) or not raw_scopes:
        return _error_response(
            400,
            "INVALID_REQUEST",
            "scopes must be a non-empty array",
            request_id,
        )

    scopes = [str(scope).strip() for scope in raw_scopes if str(scope).strip()]
    if not scopes:
        return _error_response(
            400,
            "INVALID_REQUEST",
            "scopes must contain non-empty values",
            request_id,
        )

    audience = _str_or_none(body.get("audience"))
    if audience is not None:
        return _error_response(
            400,
            "INVALID_REQUEST",
            "audience is not supported; request only approved platform scopes",
            request_id,
        )

    try:
        approved_scopes = _validate_refresh_scopes(scopes)
    except ValueError as exc:
        return _error_response(400, "INVALID_REQUEST", str(exc), request_id)

    try:
        token_payload = _exchange_obo_token(
            assertion_token=access_token,
            scopes=approved_scopes,
        )
    except ValueError as exc:
        return _error_response(503, "SERVICE_UNAVAILABLE", str(exc), request_id)
    except urllib.error.HTTPError as exc:
        detail = _extract_http_error_detail(exc)
        if exc.code in {400, 401, 403, 429}:
            error_code = {
                400: "INVALID_REQUEST",
                401: "UNAUTHENTICATED",
                403: "FORBIDDEN",
                429: "TOO_MANY_REQUESTS",
            }[exc.code]
            return _error_response(exc.code, error_code, detail, request_id)
        return _error_response(503, "SERVICE_UNAVAILABLE", detail, request_id)
    except urllib.error.URLError:
        logger.exception("Token refresh endpoint unreachable")
        return _error_response(
            503,
            "SERVICE_UNAVAILABLE",
            "Token refresh service unavailable",
            request_id,
        )

    expires_in_raw = token_payload.get("expires_in", 3600)
    try:
        expires_in = max(0, int(expires_in_raw))
    except (TypeError, ValueError):
        expires_in = 3600

    access_token_out = _str_or_none(token_payload.get("access_token"))
    if access_token_out is None:
        return _error_response(
            503,
            "SERVICE_UNAVAILABLE",
            "Token refresh response missing access_token",
            request_id,
        )

    scope = _str_or_none(token_payload.get("scope")) or " ".join(approved_scopes)

    return _response(
        200,
        {
            "accessToken": access_token_out,
            "tokenType": "Bearer",
            "expiresAt": _iso(_now_utc() + timedelta(seconds=expires_in)),
            "scope": scope,
        },
    )


def _handle_session_keepalive(
    event: dict[str, Any],
    *,
    tenant_id: str,
    app_id: str,
    request_id: str,
) -> dict[str, Any]:
    try:
        body = _require_json_body(event)
    except ValueError as exc:
        return _error_response(400, "INVALID_REQUEST", str(exc), request_id)

    session_id = _str_or_none(body.get("sessionId"))
    agent_name = _str_or_none(body.get("agentName"))

    if session_id is None:
        return _error_response(400, "INVALID_REQUEST", "sessionId is required", request_id)
    if agent_name is None:
        return _error_response(400, "INVALID_REQUEST", "agentName is required", request_id)

    logger.append_keys(sessionid=session_id)

    try:
        _ping_runtime_session(
            tenant_id=tenant_id,
            app_id=app_id,
            session_id=session_id,
            agent_name=agent_name,
        )
    except ValueError as exc:
        return _error_response(503, "SERVICE_UNAVAILABLE", str(exc), request_id)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return _error_response(404, "NOT_FOUND", "Session not found", request_id)
        logger.exception("Runtime keepalive ping failed with HTTP error")
        return _error_response(
            500, "INTERNAL_ERROR", f"Runtime ping failed: {exc.code}", request_id
        )
    except urllib.error.URLError:
        logger.exception("Runtime keepalive ping failed")
        return _error_response(
            500,
            "INTERNAL_ERROR",
            "Failed to ping runtime session",
            request_id,
        )

    return _response(
        202,
        {
            "sessionId": session_id,
            "status": "accepted",
            "expiresAt": _iso(_now_utc() + timedelta(seconds=RUNTIME_KEEPALIVE_WINDOW_SECONDS)),
        },
    )


def _exchange_obo_token(
    *,
    assertion_token: str,
    scopes: list[str],
) -> dict[str, Any]:
    endpoint = _entra_token_endpoint()
    params = {
        "client_id": _resolve_secret(
            ENTRA_CLIENT_ID_SECRET_ARN, ENTRA_CLIENT_ID, "ENTRA_CLIENT_ID"
        ),
        "client_secret": _resolve_secret(
            ENTRA_CLIENT_SECRET_SECRET_ARN, ENTRA_CLIENT_SECRET, "ENTRA_CLIENT_SECRET"
        ),
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "requested_token_use": "on_behalf_of",
        "assertion": assertion_token,
        "scope": " ".join(scopes),
    }
    return _http_post_form(endpoint, params)


def _validate_refresh_scopes(scopes: list[str]) -> list[str]:
    approved_audience = _required_env_value("ENTRA_AUDIENCE", ENTRA_AUDIENCE)
    approved_prefix = f"{approved_audience}/"
    validated: list[str] = []

    for scope in scopes:
        if not scope.startswith(approved_prefix):
            raise ValueError("scopes must target the approved platform audience")

        scope_name = scope.removeprefix(approved_prefix)
        if scope_name == ".default":
            raise ValueError("scopes must not request /.default")
        if not _ALLOWED_SCOPE_NAME_RE.fullmatch(scope_name):
            raise ValueError(f"scope '{scope}' is not an approved platform scope")
        validated.append(scope)

    return validated


def _ping_runtime_session(*, tenant_id: str, app_id: str, session_id: str, agent_name: str) -> None:
    base_url = _required_env_value("RUNTIME_PING_URL", RUNTIME_PING_URL)
    if base_url.rstrip("/").endswith("/ping"):
        ping_url = base_url
    else:
        ping_url = f"{base_url.rstrip('/')}/ping"

    headers = {
        "x-tenant-id": tenant_id,
        "x-app-id": app_id,
        "x-session-id": session_id,
        "x-agent-name": agent_name,
    }

    _http_get(ping_url, headers=headers, timeout_seconds=RUNTIME_PING_TIMEOUT_SECONDS)


def _http_post_form(url: str, form_data: dict[str, str]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(form_data).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read().decode("utf-8")
        parsed = json.loads(payload) if payload else {}
        if not isinstance(parsed, dict):
            raise ValueError("Token refresh response must be a JSON object")
        return parsed


def _http_get(url: str, *, headers: dict[str, str], timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(url=url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read().decode("utf-8")
        parsed = json.loads(payload) if payload else {}
        if not isinstance(parsed, dict):
            return {}
        return parsed


def _extract_http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = exc.read().decode("utf-8")
    except Exception:
        payload = ""

    if not payload:
        return f"Token exchange failed with HTTP {exc.code}"

    try:
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            description = _str_or_none(parsed.get("error_description"))
            if description:
                return description
            err = _str_or_none(parsed.get("error"))
            if err:
                return err
    except json.JSONDecodeError:
        pass

    return payload[:500]


def _require_json_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if body is None:
        raise ValueError("Request body is required")
    if not isinstance(body, str):
        raise ValueError("Request body must be a JSON string")

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON body") from exc

    if not isinstance(parsed, dict):
        raise ValueError("JSON body must be an object")
    return parsed


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _error_response(status_code: int, code: str, message: str, request_id: str) -> dict[str, Any]:
    return _response(
        status_code,
        {
            "error": {
                "code": code,
                "message": message,
                "requestId": request_id,
            }
        },
    )


def _tenant_and_app(event: dict[str, Any]) -> tuple[str | None, str | None]:
    auth = _authorizer(event)
    tenant_id = _str_or_none(auth.get("tenantid") or auth.get("tenantId"))
    app_id = _str_or_none(auth.get("appid") or auth.get("appId"))
    return tenant_id, app_id


def _authorizer(event: dict[str, Any]) -> dict[str, Any]:
    request_context = event.get("requestContext")
    if not isinstance(request_context, dict):
        return {}

    authorizer = request_context.get("authorizer")
    if not isinstance(authorizer, dict):
        return {}

    nested = authorizer.get("lambda")
    if isinstance(nested, dict):
        return nested

    return authorizer


def _bearer_token(event: dict[str, Any]) -> str | None:
    headers = event.get("headers")
    if not isinstance(headers, dict):
        return None

    auth_header: str | None = None
    for key, value in headers.items():
        if str(key).lower() == "authorization":
            auth_header = _str_or_none(value)
            break

    if not auth_header:
        return None

    prefix = "bearer "
    if not auth_header.lower().startswith(prefix):
        return None

    token = auth_header[len(prefix) :].strip()
    return token or None


def _http_method(event: dict[str, Any]) -> str:
    method = event.get("httpMethod")
    if method:
        return str(method).upper()

    request_context = event.get("requestContext")
    if isinstance(request_context, dict):
        http = request_context.get("http")
        if isinstance(http, dict):
            return str(http.get("method") or "").upper()

    return ""


def _path(event: dict[str, Any]) -> str:
    path = event.get("path")
    if path:
        return str(path)

    request_context = event.get("requestContext")
    if isinstance(request_context, dict):
        http = request_context.get("http")
        if isinstance(http, dict) and http.get("path"):
            return str(http["path"])

        resource_path = request_context.get("resourcePath")
        if resource_path:
            return str(resource_path)

    resource = event.get("resource")
    if resource:
        return str(resource)

    return ""


def _request_id(event: dict[str, Any], context: LambdaContext | None) -> str:
    if context is not None and getattr(context, "aws_request_id", None):
        return str(context.aws_request_id)

    request_context = event.get("requestContext")
    if isinstance(request_context, dict) and request_context.get("requestId"):
        return str(request_context["requestId"])

    return "unknown"


def _required_env_value(name: str, value: str | None) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{name} is not configured")
    return value.strip()


def _entra_token_endpoint() -> str:
    if ENTRA_TOKEN_ENDPOINT:
        return ENTRA_TOKEN_ENDPOINT

    tenant_id = _required_env_value("ENTRA_TENANT_ID", ENTRA_TENANT_ID)
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _iso(ts: datetime) -> str:
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")
