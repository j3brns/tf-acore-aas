from __future__ import annotations

import time
import uuid
from typing import Any


def scoped_token_ttl_seconds(
    value: str | None,
    *,
    logger: Any,
) -> int:
    raw_value = value or "300"
    try:
        ttl = int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid SCOPED_TOKEN_TTL_SECONDS, falling back to 300",
            extra={"value": raw_value},
        )
        return 300
    return max(1, ttl)


def validate_bearer_token(
    token: str,
    *,
    entra_audience: str | None,
    entra_issuer: str | None,
    get_jwk_client: Any,
    jwt_module: Any,
    logger: Any,
) -> dict[str, Any] | None:
    if not entra_audience or not entra_issuer:
        logger.error("ENTRA_AUDIENCE and ENTRA_ISSUER must be configured for JWT validation")
        return None

    jwk_client = get_jwk_client()
    if jwk_client is None:
        logger.error("JWK client unavailable (ENTRA_JWKS_URL missing)")
        return None

    signing_key = jwk_client.get_signing_key_from_jwt(token)
    payload = jwt_module.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=entra_audience,
        issuer=entra_issuer,
    )
    return payload if isinstance(payload, dict) else None


def issue_scoped_token(
    *,
    tenant_id: str,
    app_id: str,
    tier: str,
    acting_sub: str,
    scope_tool: str,
    mcp_session_id: str | None,
    mcp_request_id: Any,
    scoped_token_issuer: str,
    ttl_seconds: int,
    signing_key: str,
    jwt_module: Any,
) -> str:
    now = int(time.time())
    claims = {
        "iss": scoped_token_issuer,
        "aud": f"tool:{scope_tool}",
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid.uuid4()),
        "tenantid": tenant_id,
        "appid": app_id,
        "tier": tier,
        "acting_sub": acting_sub,
        "scope_tool": scope_tool,
        "mcp_session_id": mcp_session_id,
        "mcp_request_id": str(mcp_request_id),
    }
    return str(jwt_module.encode(claims, signing_key, algorithm="HS256"))
