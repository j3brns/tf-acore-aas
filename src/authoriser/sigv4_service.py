from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any


def sigv4_caller_role_arns(caller_arn: str, *, assumed_role_pattern: Any) -> set[str]:
    """Return tenant role ARN candidates for an API Gateway SigV4 caller ARN."""
    candidates = {caller_arn}
    match = assumed_role_pattern.fullmatch(caller_arn)
    if match:
        candidates.add(f"arn:aws:iam::{match.group('account_id')}:role/{match.group('role_name')}")
    return candidates


def normalise_headers(event: dict[str, Any]) -> dict[str, str]:
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


def parse_sigv4_authorization(
    auth_header: str,
    *,
    signature_pattern: Any,
    access_key_pattern: Any,
    required_signed_headers: set[str] | frozenset[str],
) -> dict[str, Any] | None:
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
    if not signature_pattern.fullmatch(signature.lower()):
        return None

    scope = credential.split("/")
    if len(scope) != 5:
        return None
    access_key, date, region, service, terminator = scope
    if not access_key_pattern.fullmatch(access_key):
        return None
    if terminator != "aws4_request":
        return None
    if not region or not service:
        return None

    signed_headers_set = {
        header.strip().lower() for header in signed_headers.split(";") if header.strip()
    }
    if not required_signed_headers.issubset(signed_headers_set):
        return None

    return {
        "access_key": access_key,
        "date": date,
        "region": region,
        "service": service,
        "signed_headers": signed_headers_set,
    }


def is_valid_sigv4_timestamp(value: str, *, max_clock_skew_seconds: int) -> bool:
    """Validate x-amz-date value and enforce clock skew."""
    try:
        ts = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return False
    skew = abs((datetime.now(UTC) - ts).total_seconds())
    return skew <= max_clock_skew_seconds


def normalise_tier(value: str | None) -> str:
    if not value:
        return "basic"
    candidate = value.strip().lower()
    if candidate in {"basic", "standard", "premium"}:
        return candidate
    return "basic"


def resolve_sigv4_tenant_binding(
    caller_arn: str,
    *,
    tenants_table: str | None,
    cache: dict[str, tuple[dict[str, str], float]],
    cache_ttl_seconds: int,
    caller_role_arns: Any,
    db_factory: Any,
    get_platform_context: Any,
    key_condition_builder: Any,
    normalise_tier: Any,
    logger: Any,
) -> dict[str, str] | None:
    """Resolve a SigV4 caller to a trusted tenant using tenant metadata."""
    if not tenants_table:
        logger.warning("TENANTS_TABLE not set; SigV4 tenant binding unavailable")
        return None

    now = time.time()
    for arn in caller_role_arns(caller_arn):
        cached = cache.get(arn)
        if cached is not None:
            binding, expiry = cached
            if now < expiry:
                return binding
            del cache[arn]

    candidate_role_arns = caller_role_arns(caller_arn)
    db = db_factory(get_platform_context())
    matches: dict[str, dict[str, str]] = {}

    try:
        for role_arn in candidate_role_arns:
            response = db.query(
                tenants_table,
                key_condition=key_condition_builder(role_arn),
                index_name="gsi-execution-role-arn",
                ProjectionExpression="PK, SK",
            )
            items = getattr(response, "items", [])
            for index_item in items:
                item = db.get_item(
                    tenants_table,
                    {"PK": index_item["PK"], "SK": index_item["SK"]},
                    ProjectionExpression="tenantId, tenant_id, appId, app_id, tier",
                )
                if not item:
                    continue
                tenant_id = str(item.get("tenantId") or item.get("tenant_id") or "").strip()
                app_id = str(item.get("appId") or item.get("app_id") or "").strip()
                tier = normalise_tier(str(item.get("tier") or "basic"))
                if tenant_id:
                    result = {"tenant_id": tenant_id, "app_id": app_id, "tier": tier}
                    matches[tenant_id] = result
                    cache[role_arn] = (result, now + cache_ttl_seconds)
    except Exception:
        logger.exception(
            "Failed to resolve SigV4 tenant binding via GSI", extra={"caller_arn": caller_arn}
        )
        return None

    if not matches:
        return None
    if len(matches) != 1:
        logger.warning(
            "SigV4 caller tenant binding not unique",
            extra={"caller_arn": caller_arn, "match_count": len(matches)},
        )
        return None
    return next(iter(matches.values()))


def handle_sigv4(
    auth_header: str,
    method_arn: str,
    event: dict[str, Any],
    *,
    parse_sigv4_authorization: Any,
    normalise_headers: Any,
    is_valid_sigv4_timestamp: Any,
    tenant_id_pattern: Any,
    resolve_sigv4_tenant_binding: Any,
    get_tenant_status: Any,
    is_admin_route: Any,
    generate_policy: Any,
    logger: Any,
) -> dict[str, Any]:
    """Validate and process AWS SigV4 machine caller context."""
    parsed = parse_sigv4_authorization(auth_header)
    if not parsed:
        logger.warning("Malformed SigV4 Authorization header")
        return generate_policy("machine", "Deny", method_arn, {})

    if parsed["service"] != "execute-api":
        logger.warning("SigV4 service must be execute-api", extra={"service": parsed["service"]})
        return generate_policy("machine", "Deny", method_arn, {})

    headers = normalise_headers(event)
    requested_tenant_id = headers.get("x-tenant-id", "")
    if not requested_tenant_id or not tenant_id_pattern.fullmatch(requested_tenant_id):
        logger.warning("Missing or invalid x-tenant-id for SigV4 request")
        return generate_policy("machine", "Deny", method_arn, {})

    amz_date = headers.get("x-amz-date", "")
    if not is_valid_sigv4_timestamp(amz_date):
        logger.warning("Invalid or stale x-amz-date for SigV4 request")
        return generate_policy("machine", "Deny", method_arn, {})

    request_context = event.get("requestContext", {})
    identity = request_context.get("identity", {}) if isinstance(request_context, dict) else {}
    if not isinstance(identity, dict):
        identity = {}

    identity_access_key = str(identity.get("accessKey", "")).strip()
    if identity_access_key and identity_access_key != parsed["access_key"]:
        logger.warning(
            "SigV4 access key mismatch between request context and Authorization header",
            extra={
                "identity_access_key": identity_access_key,
                "credential_access_key": parsed["access_key"],
            },
        )
        return generate_policy("machine", "Deny", method_arn, {})

    caller_arn = str(identity.get("userArn", "")).strip()
    caller_id = str(identity.get("caller", "")).strip()
    if not caller_arn and not caller_id:
        logger.warning("SigV4 caller identity missing from request context")
        return generate_policy("machine", "Deny", method_arn, {})

    tenant_binding = resolve_sigv4_tenant_binding(caller_arn)
    if not tenant_binding:
        logger.warning(
            "SigV4 caller has no trusted tenant binding", extra={"caller_arn": caller_arn}
        )
        return generate_policy("machine", "Deny", method_arn, {})

    tenant_id = tenant_binding["tenant_id"]
    if requested_tenant_id != tenant_id:
        logger.warning(
            "SigV4 x-tenant-id does not match trusted tenant binding",
            extra={
                "caller_arn": caller_arn,
                "requested_tenant_id": requested_tenant_id,
                "trusted_tenant_id": tenant_id,
            },
        )
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

    app_id = tenant_binding["app_id"] or identity_access_key or parsed["access_key"]
    tier = tenant_binding["tier"]
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
