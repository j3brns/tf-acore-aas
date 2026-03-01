"""
tenant_api.handler — Tenant management REST API Lambda.

Handles CRUD for tenants: create, read, update, soft-delete.
Uses data-access-lib exclusively. Publishes EventBridge events on mutations.

Implemented in TASK-017.
ADRs: ADR-012
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from botocore.exceptions import ClientError
from data_access import TenantContext, TenantScopedDynamoDB
from data_access.models import TenantStatus, TenantTier

logger = Logger(service="tenant-api")

_TENANTS_TABLE_ENV = "TENANTS_TABLE_NAME"
_EVENT_BUS_ENV = "EVENT_BUS_NAME"
_API_KEY_SECRET_PREFIX_ENV = "TENANT_API_KEY_SECRET_PREFIX"  # pragma: allowlist secret
_DELETE_RETENTION_DAYS = 30
_ADMIN_ROLES = {"Platform.Admin"}


@dataclass(frozen=True)
class CallerIdentity:
    tenant_id: str | None
    app_id: str | None
    tier: str | None
    sub: str | None
    roles: frozenset[str]
    usage_identifier_key: str | None

    @property
    def is_admin(self) -> bool:
        return bool(self.roles & _ADMIN_ROLES)


@dataclass(frozen=True)
class TenantApiDependencies:
    secretsmanager: Any
    events: Any
    usage_client: Any
    memory_provisioner: Any


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def _error(status_code: int, code: str, message: str) -> dict[str, Any]:
    return _response(status_code, {"error": {"code": code, "message": message}})


def _get_authorizer_map(event: dict[str, Any]) -> dict[str, Any]:
    request_context = event.get("requestContext", {})
    authorizer = request_context.get("authorizer", {})
    if not isinstance(authorizer, dict):
        return {}
    if "lambda" in authorizer and isinstance(authorizer["lambda"], dict):
        return authorizer["lambda"]
    return authorizer


def _parse_roles(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, list):
        return frozenset(str(v).strip() for v in value if str(v).strip())
    if isinstance(value, str):
        normalized = value.replace(",", " ").split()
        return frozenset(part.strip() for part in normalized if part.strip())
    return frozenset()


def _caller_identity(event: dict[str, Any]) -> CallerIdentity:
    auth = _get_authorizer_map(event)
    return CallerIdentity(
        tenant_id=_str_or_none(auth.get("tenantid") or auth.get("tenantId")),
        app_id=_str_or_none(auth.get("appid") or auth.get("appId")),
        tier=_str_or_none(auth.get("tier")),
        sub=_str_or_none(auth.get("sub")),
        roles=_parse_roles(auth.get("roles")),
        usage_identifier_key=_str_or_none(
            auth.get("usageIdentifierKey") or auth.get("usage_identifier_key")
        ),
    )


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_json_body(event: dict[str, Any]) -> dict[str, Any]:
    raw_body = event.get("body")
    if raw_body is None:
        raise ValueError("Request body is required")
    if not isinstance(raw_body, str):
        raise ValueError("Request body must be a JSON string")
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON body") from exc
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    return body


def _http_method(event: dict[str, Any]) -> str:
    method = event.get("httpMethod")
    if not method:
        method = event.get("requestContext", {}).get("http", {}).get("method")
    return str(method or "").upper()


def _path_tenant_id(event: dict[str, Any]) -> str | None:
    path_params = event.get("pathParameters") or {}
    if not isinstance(path_params, dict):
        return None
    return _str_or_none(path_params.get("tenantId") or path_params.get("id"))


def _tenant_pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def _tenant_key(tenant_id: str) -> dict[str, str]:
    return {"PK": _tenant_pk(tenant_id), "SK": "METADATA"}


def _tenants_table_name() -> str:
    return os.environ.get(_TENANTS_TABLE_ENV, "platform-tenants")


def _event_bus_name() -> str:
    return os.environ.get(_EVENT_BUS_ENV, "default")


def _secret_prefix() -> str:
    return os.environ.get(_API_KEY_SECRET_PREFIX_ENV, "platform/tenants")


def _dependencies() -> TenantApiDependencies:
    region = os.environ["AWS_REGION"]
    session = boto3.session.Session(region_name=region)
    return TenantApiDependencies(
        secretsmanager=session.client("secretsmanager"),
        events=session.client("events"),
        usage_client=_NoopUsageClient(),
        memory_provisioner=_NoopMemoryProvisioner(),
    )


class _NoopUsageClient:
    def get_tenant_usage(self, *, tenant_id: str, app_id: str | None) -> dict[str, Any]:
        return {"tenantId": tenant_id, "appId": app_id}


class _NoopMemoryProvisioner:
    def provision(self, *, tenant_id: str, app_id: str) -> dict[str, Any]:
        return {}


def _tenant_context_for_scope(
    *,
    tenant_id: str,
    caller: CallerIdentity,
    app_id: str | None,
) -> TenantContext:
    tier_raw = (caller.tier or TenantTier.STANDARD.value).lower()
    try:
        tier = TenantTier(tier_raw)
    except ValueError:
        tier = TenantTier.STANDARD
    return TenantContext(
        tenant_id=tenant_id,
        app_id=app_id or caller.app_id or "unknown-app",
        tier=tier,
        sub=caller.sub or "system",
    )


def _db_for_tenant(
    *,
    tenant_id: str,
    caller: CallerIdentity,
    app_id: str | None,
) -> TenantScopedDynamoDB:
    tenant_context = _tenant_context_for_scope(
        tenant_id=tenant_id,
        caller=caller,
        app_id=app_id,
    )
    return TenantScopedDynamoDB(tenant_context)


def _normalize_tier(value: Any) -> str:
    tier_text = _str_or_none(value)
    if tier_text is None:
        raise ValueError("tier is required")
    try:
        return TenantTier(tier_text.lower()).value
    except ValueError as exc:
        raise ValueError("tier must be one of: basic, standard, premium") from exc


def _normalize_status(value: Any) -> str:
    status_text = _str_or_none(value)
    if status_text is None:
        raise ValueError("status is required")
    try:
        return TenantStatus(status_text.lower()).value
    except ValueError as exc:
        raise ValueError("status must be one of: active, suspended, deleted") from exc


def _as_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc


def _require_admin(caller: CallerIdentity) -> None:
    if not caller.is_admin:
        raise PermissionError("Platform.Admin or Platform.Operator role required")


def _can_read_tenant(caller: CallerIdentity, tenant_id: str) -> bool:
    return caller.is_admin or caller.tenant_id == tenant_id


def _ddb_value(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _read_tenant_record(
    *,
    tenant_id: str,
    caller: CallerIdentity,
    app_id: str | None = None,
) -> dict[str, Any] | None:
    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    return db.get_item(_tenants_table_name(), _tenant_key(tenant_id))


def _build_update_expression(
    attributes: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    set_parts: list[str] = []
    for idx, (field, raw_value) in enumerate(attributes.items(), start=1):
        name_key = f"#n{idx}"
        value_key = f":v{idx}"
        names[name_key] = field
        values[value_key] = _ddb_value(raw_value)
        set_parts.append(f"{name_key} = {value_key}")
    return "SET " + ", ".join(set_parts), names, values


def _put_event(
    deps: TenantApiDependencies,
    *,
    detail_type: str,
    detail: dict[str, Any],
) -> None:
    deps.events.put_events(
        Entries=[
            {
                "Source": "platform.tenant_api",
                "DetailType": detail_type,
                "Detail": json.dumps(detail, default=_json_default),
                "EventBusName": _event_bus_name(),
            }
        ]
    )


def _create_api_key_secret(
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
    app_id: str,
) -> str:
    secret_name = f"{_secret_prefix().rstrip('/')}/{tenant_id}/api-key"
    secret_string = json.dumps(
        {
            "tenantId": tenant_id,
            "appId": app_id,
            "apiKey": secrets.token_urlsafe(32),
        }
    )
    response = deps.secretsmanager.create_secret(
        Name=secret_name,
        SecretString=secret_string,
        Description=f"Tenant API key for {tenant_id}",
        Tags=[
            {"Key": "tenantid", "Value": tenant_id},
            {"Key": "appid", "Value": app_id},
        ],
    )
    return str(response["ARN"])


def _serialize_tenant(item: dict[str, Any]) -> dict[str, Any]:
    record = {
        "tenantId": str(item.get("tenantId", "")),
        "appId": str(item.get("appId", "")),
        "displayName": str(item.get("displayName", "")),
        "tier": str(item.get("tier", "")),
        "status": str(item.get("status", "")),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
        "ownerEmail": item.get("ownerEmail"),
        "ownerTeam": item.get("ownerTeam"),
        "accountId": item.get("accountId"),
    }
    optional_fields = (
        "memoryStoreArn",
        "runtimeRegion",
        "fallbackRegion",
        "apiKeySecretArn",
        "monthlyBudgetUsd",
        "deletedAt",
        "purgeAtEpochSeconds",
    )
    for field in optional_fields:
        if field in item and item[field] is not None:
            record[field] = item[field]
    return record


def _handle_create(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    _require_admin(caller)
    body = _require_json_body(event)
    required = ["tenantId", "appId", "displayName", "tier", "ownerEmail", "ownerTeam", "accountId"]
    missing = [field for field in required if _str_or_none(body.get(field)) is None]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")

    tenant_id = str(body["tenantId"]).strip()
    app_id = str(body["appId"]).strip()
    now = _now_utc()
    tier = _normalize_tier(body.get("tier"))

    if _read_tenant_record(tenant_id=tenant_id, caller=caller, app_id=app_id) is not None:
        return _error(409, "CONFLICT", "Tenant already exists")

    memory_info = deps.memory_provisioner.provision(tenant_id=tenant_id, app_id=app_id) or {}
    api_key_secret_arn = _create_api_key_secret(deps, tenant_id=tenant_id, app_id=app_id)

    attributes: dict[str, Any] = {
        "tenantId": tenant_id,
        "appId": app_id,
        "displayName": str(body["displayName"]).strip(),
        "tier": tier,
        "status": TenantStatus.ACTIVE.value,
        "createdAt": _iso(now),
        "updatedAt": _iso(now),
        "ownerEmail": str(body["ownerEmail"]).strip(),
        "ownerTeam": str(body["ownerTeam"]).strip(),
        "accountId": str(body["accountId"]).strip(),
        "apiKeySecretArn": api_key_secret_arn,
    }
    if body.get("monthlyBudgetUsd") is not None:
        attributes["monthlyBudgetUsd"] = _as_float(
            body["monthlyBudgetUsd"],
            field="monthlyBudgetUsd",
        )

    for field in ("runtimeRegion", "fallbackRegion"):
        text = _str_or_none(body.get(field))
        if text is not None:
            attributes[field] = text

    memory_store_arn = _str_or_none(memory_info.get("memoryStoreArn"))
    if memory_store_arn is not None:
        attributes["memoryStoreArn"] = memory_store_arn

    update_expression, expr_names, expr_values = _build_update_expression(attributes)
    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    try:
        response = db.update_item(
            _tenants_table_name(),
            key=_tenant_key(tenant_id),
            update_expression=update_expression,
            expression_attribute_values=expr_values,
            expression_attribute_names=expr_names,
            condition_expression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return _error(409, "CONFLICT", "Tenant already exists")
        raise

    item = response.get("Attributes", {})
    _put_event(
        deps,
        detail_type="tenant.created",
        detail={"tenantId": tenant_id, "appId": app_id, "actorSub": caller.sub},
    )
    return _response(201, {"tenant": _serialize_tenant(item)})


def _usage_summary(
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
    app_id: str | None,
) -> dict[str, Any]:
    usage = deps.usage_client.get_tenant_usage(tenant_id=tenant_id, app_id=app_id)
    if not isinstance(usage, dict):
        return {}
    return usage


def _handle_list(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    if not caller.is_admin:
        # Non-admin only sees their own tenant, but in list format
        if caller.tenant_id:
            response = _handle_read(event, caller, deps, tenant_id=caller.tenant_id)
            if response["statusCode"] == 200:
                body = json.loads(response["body"])
                return _response(200, {"items": [body["tenant"]], "nextToken": None})
        return _response(200, {"items": [], "nextToken": None})

    # Admin can list all, with optional filtering
    query_params = event.get("queryStringParameters") or {}
    status_filter = _str_or_none(query_params.get("status"))
    tier_filter = _str_or_none(query_params.get("tier"))
    limit = min(int(query_params.get("limit", 50)), 100)
    next_token = query_params.get("nextToken")

    # We need a system context to scan the table (or use a GSI if available)
    # The platform-tenants table PK is TENANT#{id}, SK is METADATA.
    # Scanning is acceptable for this low-volume config table.
    db = _db_for_tenant(
        tenant_id=caller.tenant_id or "system",
        caller=caller,
        app_id=caller.app_id or "system",
    )

    scan_kwargs: dict[str, Any] = {
        "TableName": _tenants_table_name(),
        "Limit": limit,
    }
    if next_token:
        scan_kwargs["ExclusiveStartKey"] = json.loads(next_token)

    filter_exprs = []
    expr_values = {}
    expr_names = {}

    if status_filter:
        filter_exprs.append("#s = :s")
        expr_names["#s"] = "status"
        expr_values[":s"] = status_filter.lower()
    if tier_filter:
        filter_exprs.append("#t = :t")
        expr_names["#t"] = "tier"
        expr_values[":t"] = tier_filter.lower()

    if filter_exprs:
        scan_kwargs["FilterExpression"] = " AND ".join(filter_exprs)
        scan_kwargs["ExpressionAttributeNames"] = expr_names
        scan_kwargs["ExpressionAttributeValues"] = expr_values

    # Scan the table using data-access-lib
    items = db.scan(_tenants_table_name(), **scan_kwargs)

    return _response(
        200,
        {
            "items": [_serialize_tenant(item) for item in items],
            "nextToken": None,  # TODO: Implement paged scan in data-access-lib
        },
    )


def _handle_read(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not _can_read_tenant(caller, tenant_id):
        raise PermissionError("Caller may only read own tenant unless Platform.Admin")
    item = _read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return _error(404, "NOT_FOUND", "Tenant not found")
    tenant = _serialize_tenant(item)
    tenant["usage"] = _usage_summary(
        deps,
        tenant_id=tenant_id,
        app_id=_str_or_none(item.get("appId")),
    )
    if caller.usage_identifier_key:
        tenant["usage"]["usageIdentifierKey"] = caller.usage_identifier_key
    return _response(200, {"tenant": tenant})


def _handle_update(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    _require_admin(caller)
    existing = _read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return _error(404, "NOT_FOUND", "Tenant not found")

    body = _require_json_body(event)
    allowed = {"tier", "monthlyBudgetUsd", "status"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ValueError(f"Unsupported update field(s): {', '.join(unknown)}")
    if not body:
        raise ValueError("At least one update field is required")

    attrs: dict[str, Any] = {"updatedAt": _iso(_now_utc())}
    if "tier" in body:
        attrs["tier"] = _normalize_tier(body["tier"])
    if "monthlyBudgetUsd" in body:
        attrs["monthlyBudgetUsd"] = _as_float(body["monthlyBudgetUsd"], field="monthlyBudgetUsd")
    if "status" in body:
        attrs["status"] = _normalize_status(body["status"])

    update_expression, expr_names, expr_values = _build_update_expression(attrs)
    db = _db_for_tenant(
        tenant_id=tenant_id,
        caller=caller,
        app_id=_str_or_none(existing.get("appId")),
    )
    response = db.update_item(
        _tenants_table_name(),
        key=_tenant_key(tenant_id),
        update_expression=update_expression,
        expression_attribute_values=expr_values,
        expression_attribute_names=expr_names,
        condition_expression="attribute_exists(PK) AND attribute_exists(SK)",
    )
    item = response.get("Attributes", {})

    old_tier = _str_or_none(existing.get("tier"))
    new_tier = _str_or_none(item.get("tier"))
    detail_type = "tenant.updated"
    detail: dict[str, Any] = {"tenantId": tenant_id, "actorSub": caller.sub}
    if old_tier != new_tier and new_tier is not None:
        detail_type = "tenant.tier_changed"
        detail["oldTier"] = old_tier
        detail["newTier"] = new_tier
    _put_event(deps, detail_type=detail_type, detail=detail)
    return _response(200, {"tenant": _serialize_tenant(item)})


def _handle_delete(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    _require_admin(caller)
    existing = _read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return _error(404, "NOT_FOUND", "Tenant not found")

    now = _now_utc()
    purge_at = int((now + timedelta(days=_DELETE_RETENTION_DAYS)).timestamp())
    attrs = {
        "status": TenantStatus.DELETED.value,
        "updatedAt": _iso(now),
        "deletedAt": _iso(now),
        "purgeAtEpochSeconds": purge_at,
    }
    db = _db_for_tenant(
        tenant_id=tenant_id,
        caller=caller,
        app_id=_str_or_none(existing.get("appId")),
    )
    update_expression, expr_names, expr_values = _build_update_expression(attrs)
    response = db.update_item(
        _tenants_table_name(),
        key=_tenant_key(tenant_id),
        update_expression=update_expression,
        expression_attribute_values=expr_values,
        expression_attribute_names=expr_names,
        condition_expression="attribute_exists(PK) AND attribute_exists(SK)",
    )
    item = response.get("Attributes", {})
    _put_event(
        deps,
        detail_type="tenant.deleted",
        detail={
            "tenantId": tenant_id,
            "actorSub": caller.sub,
            "retentionDays": _DELETE_RETENTION_DAYS,
            "purgeAtEpochSeconds": purge_at,
        },
    )
    return _response(200, {"tenant": _serialize_tenant(item)})


def _handle_audit_export(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    _require_admin(caller)
    existing = _read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return _error(404, "NOT_FOUND", "Tenant not found")

    # In a real implementation, this would trigger a background export of
    # platform-invocations to S3 and return a presigned URL.
    # For now, we return a mock response as per the "future implementation" note in openapi.yaml.
    now = _now_utc()
    expires_at = now + timedelta(hours=1)
    return _response(
        200,
        {
            "tenantId": tenant_id,
            "downloadUrl": f"https://s3.amazonaws.com/mock-export-{tenant_id}.csv",
            "expiresAt": _iso(expires_at),
        },
    )


def _handle_platform_failover(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    _require_admin(caller)
    body = _require_json_body(event)
    target_region = _str_or_none(body.get("targetRegion"))
    lock_id = _str_or_none(body.get("lockId"))

    if not target_region or not lock_id:
        raise ValueError("targetRegion and lockId are required")

    # TODO: Verify lock_id against platform-ops-locks table
    # TODO: Update SSM /platform/config/runtime-region

    logger.info(
        "Platform failover triggered",
        extra={"target_region": target_region, "lock_id": lock_id, "actor": caller.sub},
    )

    return _response(200, {"status": "initiated", "region": target_region})


def _handle_platform_quota(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    _require_admin(caller)

    # In a real implementation, this would call AgentCore service quotas
    # or CloudWatch metrics for ConcurrentSessions.
    return _response(
        200,
        {
            "utilisation": [
                {
                    "region": "eu-west-1",
                    "quotaName": "ConcurrentSessions",
                    "currentValue": 5,
                    "limit": 25,
                    "utilisationPercentage": 20.0,
                },
                {
                    "region": "eu-central-1",
                    "quotaName": "ConcurrentSessions",
                    "currentValue": 0,
                    "limit": 25,
                    "utilisationPercentage": 0.0,
                },
            ]
        },
    )


def _handle_platform_split_accounts(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    # Platform.Admin only for this one
    if "Platform.Admin" not in caller.roles:
        raise PermissionError("Platform.Admin role required")

    body = _require_json_body(event)
    tier = _normalize_tier(body.get("tier"))
    target_account_id = _str_or_none(body.get("targetAccountId"))

    if not target_account_id:
        raise ValueError("targetAccountId is required")

    # In a real implementation, this would trigger an Step Function or async job
    # to move tenants of the specified tier to a new account.
    job_id = f"job-split-{secrets.token_hex(4)}"
    logger.info(
        "Account split initiated",
        extra={"tier": tier, "target_account_id": target_account_id, "job_id": job_id},
    )

    return _response(202, {"status": "initiated", "jobId": job_id})


def _request_path(event: dict[str, Any]) -> str:
    path = event.get("path")
    if not path:
        path = event.get("requestContext", {}).get("http", {}).get("path")
    return str(path or "").rstrip("/")


@logger.inject_lambda_context(clear_state=True, log_event=False)
def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    caller = _caller_identity(event)
    deps = _dependencies()
    logger.append_keys(appid=caller.app_id or "unknown", tenantid=caller.tenant_id or "unknown")

    method = _http_method(event)
    tenant_id = _path_tenant_id(event)
    path = _request_path(event)

    try:
        # Platform management routes
        if path == "/v1/platform/failover" and method == "POST":
            return _handle_platform_failover(event, caller, deps)
        if path == "/v1/platform/quota" and method == "GET":
            return _handle_platform_quota(caller, deps)
        if path == "/v1/platform/quota/split-accounts" and method == "POST":
            return _handle_platform_split_accounts(event, caller, deps)

        # Tenant management routes
        if path == "/v1/tenants":
            if method == "POST":
                return _handle_create(event, caller, deps)
            if method == "GET":
                return _handle_list(event, caller, deps)

        if tenant_id is not None:
            if path.endswith("/audit-export") and method == "GET":
                return _handle_audit_export(caller, deps, tenant_id=tenant_id)

            if method == "GET":
                return _handle_read(event, caller, deps, tenant_id=tenant_id)
            if method in {"PATCH", "PUT"}:
                return _handle_update(event, caller, deps, tenant_id=tenant_id)
            if method == "DELETE":
                return _handle_delete(caller, deps, tenant_id=tenant_id)

        return _error(405, "METHOD_NOT_ALLOWED", "Unsupported tenant API route")
    except PermissionError as exc:
        return _error(403, "FORBIDDEN", str(exc))
    except ValueError as exc:
        return _error(400, "BAD_REQUEST", str(exc))
    except ClientError as exc:
        logger.exception("AWS client error in tenant API handler")
        return _error(502, "AWS_CLIENT_ERROR", exc.response.get("Error", {}).get("Code", "Unknown"))
    except Exception:
        logger.exception("Unhandled tenant API handler error")
        return _error(500, "INTERNAL_ERROR", "Internal server error")
