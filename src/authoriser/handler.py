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
from datetime import UTC, datetime
from typing import Any

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

SIGV4_REQUIRED_SIGNED_HEADERS = frozenset({"host", "x-amz-date", "x-tenant-id"})
SIGV4_MAX_CLOCK_SKEW_SECONDS = int(os.environ.get("SIGV4_MAX_CLOCK_SKEW_SECONDS", "300"))
_SIGV4_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
_SIGV4_ACCESS_KEY_RE = re.compile(r"^[A-Z0-9]{16,128}$")
_SIGV4_TENANT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,127}$")


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


def _normalise_headers(event: dict[str, Any]) -> dict[str, str]:
    """Return request headers as a lowercase key map."""
    raw_headers = event.get("headers") or {}
    if not isinstance(raw_headers, dict):
        return {}
    normalised: dict[str, str] = {}
    for raw_key, raw_value in raw_headers.items():
        key = str(raw_key).strip().lower()
        value = str(raw_value).strip() if raw_value is not None else ""
        if key:
            normalised[key] = value
    return normalised


def _parse_sigv4_authorization(auth_header: str) -> dict[str, Any] | None:
    """Parse AWS SigV4 Authorization header into structured fields."""
    if not auth_header.startswith("AWS4-HMAC-SHA256 "):
        return None

    payload = auth_header.removeprefix("AWS4-HMAC-SHA256 ").strip()
    pieces = [part.strip() for part in payload.split(",") if part.strip()]
    parsed: dict[str, str] = {}
    for piece in pieces:
        if "=" not in piece:
            continue
        key, value = piece.split("=", 1)
        parsed[key.strip()] = value.strip()

    credential = parsed.get("Credential")
    signed_headers = parsed.get("SignedHeaders")
    signature = parsed.get("Signature")
    if not credential or not signed_headers or not signature:
        return None
    if not _SIGV4_SIGNATURE_RE.fullmatch(signature.lower()):
        return None

    scope = credential.split("/")
    if len(scope) != 5:
        return None
    access_key, date, region, service, terminator = scope
    if not _SIGV4_ACCESS_KEY_RE.fullmatch(access_key):
        return None
    if terminator != "aws4_request":
        return None
    if not re.fullmatch(r"\d{8}", date):
        return None
    if not region or not service:
        return None

    signed_headers_set = {
        header.strip().lower() for header in signed_headers.split(";") if header.strip()
    }
    if not SIGV4_REQUIRED_SIGNED_HEADERS.issubset(signed_headers_set):
        return None

    return {
        "access_key": access_key,
        "date": date,
        "region": region,
        "service": service,
        "signed_headers": signed_headers_set,
    }


def _is_valid_sigv4_timestamp(value: str) -> bool:
    """Validate x-amz-date value and enforce clock skew."""
    try:
        ts = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return False
    skew = abs((datetime.now(UTC) - ts).total_seconds())
    return skew <= SIGV4_MAX_CLOCK_SKEW_SECONDS


def _normalise_tier(value: str | None) -> str:
    if not value:
        return "basic"
    candidate = value.strip().lower()
    if candidate in {"basic", "standard", "premium"}:
        return candidate
    return "basic"


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
    """Validate and process AWS SigV4 machine caller context."""
    parsed = _parse_sigv4_authorization(auth_header)
    if not parsed:
        logger.warning("Malformed SigV4 Authorization header")
        return generate_policy("machine", "Deny", method_arn, {})

    if parsed["service"] != "execute-api":
        logger.warning("SigV4 service must be execute-api", extra={"service": parsed["service"]})
        return generate_policy("machine", "Deny", method_arn, {})

    headers = _normalise_headers(event)
    tenant_id = headers.get("x-tenant-id", "")
    if not tenant_id or not _SIGV4_TENANT_ID_RE.fullmatch(tenant_id):
        logger.warning("Missing or invalid x-tenant-id for SigV4 request")
        return generate_policy("machine", "Deny", method_arn, {})

    amz_date = headers.get("x-amz-date", "")
    if not _is_valid_sigv4_timestamp(amz_date):
        logger.warning("Invalid or stale x-amz-date for SigV4 request")
        return generate_policy("machine", "Deny", method_arn, {})

    request_context = event.get("requestContext", {})
    identity = request_context.get("identity", {}) if isinstance(request_context, dict) else {}
    if not isinstance(identity, dict):
        identity = {}

    identity_access_key = str(identity.get("accessKey", "")).strip()
    if identity_access_key and identity_access_key != parsed["access_key"]:
        mismatch = {
            "identity_access_key": identity_access_key,
            "credential_access_key": parsed["access_key"],
        }
        logger.warning(
            "SigV4 access key mismatch between request context and Authorization header",
            extra=mismatch,
        )
        return generate_policy("machine", "Deny", method_arn, {})

    # Token authoriser events can omit full request context.
    # If API Gateway did not provide caller identity metadata, reject as unverifiable.
    caller_arn = str(identity.get("userArn", "")).strip()
    caller_id = str(identity.get("caller", "")).strip()
    if not caller_arn and not caller_id:
        logger.warning("SigV4 caller identity missing from request context")
        return generate_policy("machine", "Deny", method_arn, {})

    status = get_tenant_status(tenant_id)
    if status != "active":
        logger.error(
            "Tenant not active for SigV4 request",
            extra={"tenant_id": tenant_id, "status": status},
        )
        return generate_policy("machine", "Deny", method_arn, {})

    if is_admin_route(method_arn):
        logger.warning("SigV4 caller denied on admin route", extra={"tenant_id": tenant_id})
        return generate_policy("machine", "Deny", method_arn, {})

    app_id = headers.get("x-app-id") or identity_access_key or parsed["access_key"]
    tier = _normalise_tier(headers.get("x-tier"))
    actor = caller_arn or caller_id or f"sigv4:{parsed['access_key']}"
    auth_context = {
        "tenantid": tenant_id,
        "appid": app_id,
        "tier": tier,
        "sub": actor,
        "roles": json.dumps(["Machine.Invoke"]),
        "usageIdentifierKey": tenant_id,
    }

    logger.append_keys(tenant_id=tenant_id, app_id=app_id)
    logger.info("SigV4 authentication successful", extra={"tenant_id": tenant_id, "actor": actor})
    return generate_policy(actor, "Allow", method_arn, auth_context)
