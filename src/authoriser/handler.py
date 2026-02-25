"""
authoriser.handler — Lambda authoriser for Entra JWT and SigV4 paths.

Validates Bearer JWTs from Microsoft Entra ID and SigV4 signatures from
machine callers. Returns tenant context for downstream Lambdas.

Implemented in TASK-016.
ADRs: ADR-002, ADR-004, ADR-013
"""

import json
import os
from typing import Any, Optional

import boto3
import jwt
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from jwt import PyJWKClient

logger = Logger(service="authoriser")
tracer = Tracer()

# Environment variables (baked into layer or set in CDK)
ENTRA_JWKS_URL = os.environ.get("ENTRA_JWKS_URL")
ENTRA_AUDIENCE = os.environ.get("ENTRA_AUDIENCE")
ENTRA_ISSUER = os.environ.get("ENTRA_ISSUER")
TENANTS_TABLE = os.environ.get("TENANTS_TABLE")

# Global clients — connection reuse across warm starts
_jwk_client: PyJWKClient | None = None
_dynamodb_resource = None


def get_jwk_client() -> PyJWKClient | None:
    """Lazy initialization of PyJWKClient."""
    global _jwk_client
    if _jwk_client is None and ENTRA_JWKS_URL:
        # Cache JWKS for 5 minutes (300s) as per ARCHITECTURE.md
        _jwk_client = PyJWKClient(ENTRA_JWKS_URL, cache_jwk_set=True, lifespan=300)
    return _jwk_client


def get_dynamodb():
    """Lazy initialization of boto3 resource."""
    global _dynamodb_resource
    if _dynamodb_resource is None:
        region = os.environ.get("AWS_REGION", "eu-west-2")
        _dynamodb_resource = boto3.resource("dynamodb", region_name=region)
    return _dynamodb_resource


def generate_policy(
    principal_id: str, effect: str, method_arn: str, context: dict[str, Any]
) -> dict[str, Any]:
    """Generate an IAM policy for API Gateway authoriser."""
    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": method_arn,
                }
            ],
        },
        "context": context,
    }


def get_tenant_status(tenant_id: str) -> str | None:
    """Fetch tenant status from DynamoDB.

    The authoriser runs before a TenantContext exists, so it uses the
    system-level DynamoDB client directly.
    """
    if not TENANTS_TABLE:
        logger.warning("TENANTS_TABLE not set, assuming active (dev mode)")
        return "active"

    try:
        table = get_dynamodb().Table(TENANTS_TABLE)
        response = table.get_item(Key={"PK": f"TENANT#{tenant_id}", "SK": "METADATA"})
        item = response.get("Item")
        if item:
            status = item.get("status")
            return str(status) if status is not None else None
    except Exception:
        logger.exception("Failed to fetch tenant status", extra={"tenant_id": tenant_id})
    return None


def is_admin_route(method_arn: str) -> bool:
    """Check if the route is an admin/operator route (ADR-013)."""
    # method_arn format:
    # arn:aws:execute-api:{region}:{account}:{apiId}/{stage}/{method}/{resourcePath}
    parts = method_arn.split("/", 3)
    if len(parts) < 4:
        return False
    path = parts[3]
    # Admin routes include tenant management and platform operations
    return path.startswith("v1/tenants") or path.startswith("v1/platform")


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda Authoriser entry point."""
    method_arn = event["methodArn"]

    # Extract token from Authorization header or authorizationToken (ADR-002)
    auth_header = event.get("authorizationToken") or event.get("headers", {}).get("Authorization")

    if not auth_header:
        logger.warning("Missing Authorization header")
        return generate_policy("user", "Deny", method_arn, {})

    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        return handle_jwt(token, method_arn)
    elif auth_header.startswith("AWS4-HMAC-SHA256"):
        return handle_sigv4(auth_header, method_arn, event)
    else:
        # Fallback for TOKEN authorisers where the header value is passed directly as the token
        return handle_jwt(auth_header, method_arn)


def handle_jwt(token: str, method_arn: str) -> dict[str, Any]:
    """Validate and process Entra JWT (ADR-002)."""
    try:
        jwk_client = get_jwk_client()
        if not jwk_client:
            logger.error("JWK client not initialized (ENTRA_JWKS_URL missing)")
            return generate_policy("user", "Deny", method_arn, {})

        signing_key = jwk_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=ENTRA_AUDIENCE,
            issuer=ENTRA_ISSUER,
        )

        tenant_id = payload.get("tenantid")
        app_id = payload.get("appid")
        tier = payload.get("tier", "basic")
        sub = payload.get("sub", "unknown")
        roles = payload.get("roles", [])

        if not tenant_id or not app_id:
            logger.error("Missing tenantid or appid in token", extra={"payload": payload})
            return generate_policy(sub, "Deny", method_arn, {})

        # Check tenant status (Isolation Layer 1)
        status = get_tenant_status(tenant_id)
        if status != "active":
            logger.error("Tenant not active", extra={"tenant_id": tenant_id, "status": status})
            return generate_policy(sub, "Deny", method_arn, {})

        # RBAC check for admin routes (ADR-013)
        if is_admin_route(method_arn):
            if "Platform.Admin" not in roles and "Platform.Operator" not in roles:
                logger.error(
                    "User lacks required roles for admin route",
                    extra={"sub": sub, "roles": roles, "method_arn": method_arn},
                )
                return generate_policy(sub, "Deny", method_arn, {})

        # Prepare authoriser context for downstream Lambdas
        auth_context = {
            "tenantid": tenant_id,
            "appid": app_id,
            "tier": tier,
            "sub": sub,
            "roles": json.dumps(roles),
            "usageIdentifierKey": tenant_id,  # Mapped to API Gateway usage plan key
        }

        # Inject context into all subsequent log lines (structured logging mandate)
        logger.append_keys(tenant_id=tenant_id, app_id=app_id)

        logger.info("Authentication successful", extra={"tenant_id": tenant_id, "sub": sub})
        return generate_policy(sub, "Allow", method_arn, auth_context)

    except jwt.ExpiredSignatureError:
        logger.warning("JWT has expired")
        return generate_policy("user", "Deny", method_arn, {})
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT: {str(e)}")
        return generate_policy("user", "Deny", method_arn, {})
    except Exception:
        logger.exception("Unexpected error during JWT validation")
        return generate_policy("user", "Deny", method_arn, {})


def handle_sigv4(auth_header: str, method_arn: str, event: dict[str, Any]) -> dict[str, Any]:
    """Validate and process AWS SigV4 (ADR-004 Stub)."""
    # TODO: Implement SigV4 validation (ADR-004)
    # This requires signature validation logic or delegating to a trusted service.
    # SigV4 is used for machine-to-machine authentication.
    logger.warning("SigV4 path requested but not yet implemented", extra={"method_arn": method_arn})
    return generate_policy("machine", "Deny", method_arn, {})
