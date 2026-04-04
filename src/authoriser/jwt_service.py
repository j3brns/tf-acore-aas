from __future__ import annotations

import json
from typing import Any


def handle_jwt(
    token: str,
    method_arn: str,
    *,
    get_jwk_client: Any,
    generate_policy: Any,
    get_tenant_status: Any,
    is_admin_route: Any,
    is_platform_route: Any,
    entra_audience: str | None,
    entra_issuer: str | None,
    platform_tenant_id: str,
    logger: Any,
    jwt_module: Any,
) -> dict[str, Any]:
    """Validate and process an Entra JWT."""
    try:
        jwk_client = get_jwk_client()
        if not jwk_client:
            logger.error("JWK client not initialized (ENTRA_JWKS_URL missing)")
            return generate_policy("user", "Deny", method_arn, {})

        signing_key = jwk_client.get_signing_key_from_jwt(token)
        payload = jwt_module.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=entra_audience,
            issuer=entra_issuer,
        )

        tenant_id = payload.get("tenantid")
        app_id = payload.get("appid")
        tier = payload.get("tier", "basic")
        sub = payload.get("sub", "unknown")
        roles = payload.get("roles", [])

        if not tenant_id or not app_id:
            logger.error(
                "Missing tenantid or appid in token",
                extra={"present_claims": sorted(str(key) for key in payload.keys())},
            )
            return generate_policy(sub, "Deny", method_arn, {})

        effective_tenant_id = platform_tenant_id if is_platform_route(method_arn) else tenant_id
        status = get_tenant_status(effective_tenant_id)
        if status != "active":
            logger.error(
                "Tenant not active",
                extra={"tenant_id": effective_tenant_id, "status": status},
            )
            return generate_policy(sub, "Deny", method_arn, {})

        if is_admin_route(method_arn):
            if "Platform.Admin" not in roles and "Platform.Operator" not in roles:
                logger.error(
                    "User lacks required roles for admin route",
                    extra={"sub": sub, "roles": roles, "method_arn": method_arn},
                )
                return generate_policy(sub, "Deny", method_arn, {})

        auth_context = {
            "tenantid": effective_tenant_id,
            "appid": app_id,
            "tier": tier,
            "sub": sub,
            "roles": json.dumps(roles),
            "usageIdentifierKey": effective_tenant_id,
        }

        logger.append_keys(tenant_id=effective_tenant_id, app_id=app_id)
        logger.info(
            "Authentication successful",
            extra={"tenant_id": effective_tenant_id, "sub": sub},
        )
        return generate_policy(sub, "Allow", method_arn, auth_context)
    except jwt_module.ExpiredSignatureError:
        logger.warning("JWT has expired")
        return generate_policy("user", "Deny", method_arn, {})
    except jwt_module.InvalidTokenError as exc:
        logger.warning("Invalid JWT", extra={"error": str(exc)})
        return generate_policy("user", "Deny", method_arn, {})
    except Exception:
        logger.exception("Unexpected error during JWT validation")
        return generate_policy("user", "Deny", method_arn, {})
