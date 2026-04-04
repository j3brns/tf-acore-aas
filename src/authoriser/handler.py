"""
authoriser.handler — Lambda authoriser for Entra JWT and SigV4 paths.

Validates Bearer JWTs from Microsoft Entra ID and SigV4 signatures from
machine callers. Returns tenant context for downstream Lambdas.

Implemented in TASK-016.
ADRs: ADR-002, ADR-004, ADR-013
"""

import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import jwt
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from boto3.dynamodb.conditions import Attr, Key
from data_access import ControlPlaneDynamoDB, TenantContext, TenantTier
from jwt import PyJWKClient

from src.authoriser import jwt_service, sigv4_service

logger = Logger(service="authoriser")
tracer = Tracer()

# Environment variables (baked into layer or set in CDK)
ENTRA_JWKS_URL = os.environ.get("ENTRA_JWKS_URL")
ENTRA_AUDIENCE = os.environ.get("ENTRA_AUDIENCE")
ENTRA_ISSUER = os.environ.get("ENTRA_ISSUER")
TENANTS_TABLE = os.environ.get("TENANTS_TABLE")

# Global clients — connection reuse across warm starts
_jwk_client: PyJWKClient | None = None

# CR005: In-memory TTL cache for SigV4 ARN→tenant binding.
# Avoids a full DynamoDB table scan on every machine-auth request.
# TTL matches the SSM config cache (60 s) documented in ARCHITECTURE.md.
_SIGV4_BINDING_CACHE_TTL_SECONDS = int(os.environ.get("SIGV4_BINDING_CACHE_TTL_SECONDS", "60"))
_sigv4_binding_cache: dict[str, tuple[dict[str, str], float]] = {}

SIGV4_REQUIRED_SIGNED_HEADERS = frozenset({"host", "x-amz-date", "x-tenant-id"})
SIGV4_MAX_CLOCK_SKEW_SECONDS = int(os.environ.get("SIGV4_MAX_CLOCK_SKEW_SECONDS", "300"))
_SIGV4_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
_SIGV4_ACCESS_KEY_RE = re.compile(r"^[A-Z0-9]{16,128}$")
_SIGV4_TENANT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,127}$")
_SIGV4_ASSUMED_ROLE_ARN_RE = re.compile(
    r"^arn:aws:sts::(?P<account_id>\d{12}):assumed-role/(?P<role_name>[^/]+)/[^/]+$"
)
_PLATFORM_TENANT_ID = "platform"


def _aws_region() -> str:
    return os.environ["AWS_REGION"]


def get_platform_context() -> TenantContext:
    return TenantContext(
        tenant_id=_PLATFORM_TENANT_ID,
        app_id="platform-authoriser",
        tier=TenantTier.PREMIUM,
        sub="authoriser-lambda",
    )


def get_jwk_client() -> PyJWKClient | None:
    """Lazy initialization of PyJWKClient."""
    global _jwk_client
    if _jwk_client is None and ENTRA_JWKS_URL:
        # Cache JWKS for 5 minutes (300s) as per ARCHITECTURE.md
        _jwk_client = PyJWKClient(ENTRA_JWKS_URL, cache_jwk_set=True, lifespan=300)
    return _jwk_client


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

    The authoriser runs before a caller TenantContext exists, so it uses
    the reserved platform control-plane context.
    """
    if not TENANTS_TABLE:
        logger.warning("TENANTS_TABLE not set, assuming active (dev mode)")
        return "active"

    try:
        db = ControlPlaneDynamoDB(get_platform_context())
        item = db.get_item(TENANTS_TABLE, {"PK": f"TENANT#{tenant_id}", "SK": "METADATA"})
        if item:
            status = item.get("status")
            return str(status) if status is not None else None
    except Exception:
        logger.exception("Failed to fetch tenant status", extra={"tenant_id": tenant_id})
    return None


def _sigv4_caller_role_arns(caller_arn: str) -> set[str]:
    return sigv4_service.sigv4_caller_role_arns(
        caller_arn, assumed_role_pattern=_SIGV4_ASSUMED_ROLE_ARN_RE
    )


def resolve_sigv4_tenant_binding(caller_arn: str) -> dict[str, str] | None:
    return sigv4_service.resolve_sigv4_tenant_binding(
        caller_arn,
        tenants_table=TENANTS_TABLE,
        cache=_sigv4_binding_cache,
        cache_ttl_seconds=_SIGV4_BINDING_CACHE_TTL_SECONDS,
        caller_role_arns=_sigv4_caller_role_arns,
        db_factory=ControlPlaneDynamoDB,
        get_platform_context=get_platform_context,
        key_condition_builder=lambda role_arn: Key("executionRoleArn").eq(role_arn),
        normalise_tier=_normalise_tier,
        logger=logger,
    )


def _normalise_headers(event: dict[str, Any]) -> dict[str, str]:
    return sigv4_service.normalise_headers(event)


def _parse_sigv4_authorization(auth_header: str) -> dict[str, Any] | None:
    return sigv4_service.parse_sigv4_authorization(
        auth_header,
        signature_pattern=_SIGV4_SIGNATURE_RE,
        access_key_pattern=_SIGV4_ACCESS_KEY_RE,
        required_signed_headers=SIGV4_REQUIRED_SIGNED_HEADERS,
    )


def _is_valid_sigv4_timestamp(value: str) -> bool:
    return sigv4_service.is_valid_sigv4_timestamp(
        value, max_clock_skew_seconds=SIGV4_MAX_CLOCK_SKEW_SECONDS
    )


def _normalise_tier(value: str | None) -> str:
    return sigv4_service.normalise_tier(value)


def is_admin_route(method_arn: str) -> bool:
    """Check if the route is an admin/operator route (ADR-013)."""
    # method_arn format:
    # arn:aws:execute-api:{region}:{account}:{apiId}/{stage}/{method}/{resourcePath}
    parts = method_arn.split("/", 3)
    if len(parts) < 4:
        return False

    method = str(parts[2]).upper()
    path = str(parts[3]).strip("/")

    # All /v1/platform routes are operator/admin only.
    if path.startswith("v1/platform"):
        return True

    # /v1/tenants collection:
    # - POST is admin/operator only
    # - GET is caller-scoped and allowed for non-admin
    if path == "v1/tenants":
        return method == "POST"

    # /v1/tenants/{tenantId}/audit-export is admin/operator only.
    if path.startswith("v1/tenants/") and path.endswith("/audit-export"):
        return True

    # /v1/tenants/{tenantId} mutations are admin/operator only.
    if path.startswith("v1/tenants/") and method in {"PATCH", "PUT", "DELETE"}:
        return True

    # Reads (e.g. GET /v1/tenants/{tenantId}) are enforced downstream by tenant-api
    # own-tenant checks and should not be blocked here.
    return False


def is_platform_route(method_arn: str) -> bool:
    parts = method_arn.split("/", 3)
    if len(parts) < 4:
        return False
    path = str(parts[3]).strip("/")
    return path.startswith("v1/platform")


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
    return jwt_service.handle_jwt(
        token,
        method_arn,
        get_jwk_client=get_jwk_client,
        generate_policy=generate_policy,
        get_tenant_status=get_tenant_status,
        is_admin_route=is_admin_route,
        is_platform_route=is_platform_route,
        entra_audience=ENTRA_AUDIENCE,
        entra_issuer=ENTRA_ISSUER,
        platform_tenant_id=_PLATFORM_TENANT_ID,
        logger=logger,
        jwt_module=jwt,
    )


def handle_sigv4(auth_header: str, method_arn: str, event: dict[str, Any]) -> dict[str, Any]:
    return sigv4_service.handle_sigv4(
        auth_header,
        method_arn,
        event,
        parse_sigv4_authorization=_parse_sigv4_authorization,
        normalise_headers=_normalise_headers,
        is_valid_sigv4_timestamp=_is_valid_sigv4_timestamp,
        tenant_id_pattern=_SIGV4_TENANT_ID_RE,
        resolve_sigv4_tenant_binding=resolve_sigv4_tenant_binding,
        get_tenant_status=get_tenant_status,
        is_admin_route=is_admin_route,
        generate_policy=generate_policy,
        logger=logger,
    )
