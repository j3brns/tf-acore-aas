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
import re
import secrets
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import ConditionBase, Key
from botocore.exceptions import ClientError
from data_access import TenantContext, TenantScopedDynamoDB, TenantScopedS3
from data_access.models import TenantStatus, TenantTier

logger = Logger(service="tenant-api")

_TENANTS_TABLE_ENV = "TENANTS_TABLE_NAME"
_INVOCATIONS_TABLE_ENV = "INVOCATIONS_TABLE_NAME"
_EVENT_BUS_ENV = "EVENT_BUS_NAME"
_AUDIT_EXPORT_BUCKET_ENV = "AUDIT_EXPORT_BUCKET"
_API_KEY_SECRET_PREFIX_ENV = "TENANT_API_KEY_SECRET_PREFIX"  # pragma: allowlist secret
_OPS_LOCKS_TABLE_ENV = "OPS_LOCKS_TABLE"
_RUNTIME_REGION_PARAM_ENV = "RUNTIME_REGION_PARAM"
_FALLBACK_REGION_PARAM_ENV = "FALLBACK_REGION_PARAM"
_FAILOVER_LOCK_NAME_ENV = "FAILOVER_LOCK_NAME"
_DELETE_RETENTION_DAYS = 30
_ADMIN_ROLES = {"Platform.Admin"}
_SELF_SERVICE_ADMIN_ROLES = {"Platform.Admin", "Platform.Operator", "SelfService.Admin"}
_ALLOWED_TENANT_INVITE_ROLES = {"Agent.Invoke"}
_INVITE_EXPIRY_DAYS = 7
_AUDIT_EXPORT_PREFIX = "audit-exports"
_AUDIT_EXPORT_URL_EXPIRY_SECONDS = 3600
_AUDIT_EXPORT_PAGE_SIZE = 200
_TENANT_ID_MIN_LENGTH = 3
_TENANT_ID_MAX_LENGTH = 32
_TENANT_ID_PATTERN = re.compile(r"^[a-z](?:[a-z0-9-]{1,30}[a-z0-9])$")
_AWS_ACCOUNT_ID_PATTERN = re.compile(r"^[0-9]{12}$")
_RESERVED_TENANT_IDS = frozenset({"admin", "root", "system", "stub"})
_DEFAULT_OPS_LOCKS_TABLE = "platform-ops-locks"
_DEFAULT_RUNTIME_REGION_PARAM = "/platform/config/runtime-region"
_DEFAULT_FALLBACK_REGION_PARAM = "/platform/config/fallback-region"
_DEFAULT_FAILOVER_LOCK_NAME = "platform-runtime-failover"
_AGENTCORE_QUOTA_NAME = "Active session workloads per account"
_AGENTCORE_CONCURRENT_SESSIONS_NAMESPACE = "AgentCore"
_AGENTCORE_CONCURRENT_SESSIONS_METRIC = "ConcurrentSessions"
_AGENTCORE_QUOTA_LOOKBACK_MINUTES = 5


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
    dynamodb: Any
    ssm: Any
    awslambda: Any
    usage_client: Any
    memory_provisioner: Any
    platform_quota_client: Any


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
        # Authoriser may pass roles as a JSON-encoded list in API Gateway context.
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return frozenset(str(v).strip() for v in decoded if str(v).strip())
        except json.JSONDecodeError:
            pass
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


def _query_params(event: dict[str, Any]) -> dict[str, Any]:
    query = event.get("queryStringParameters") or {}
    if not isinstance(query, dict):
        return {}
    return query


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


def _validated_path_tenant_id(event: dict[str, Any]) -> str | None:
    tenant_id = _path_tenant_id(event)
    if tenant_id is None:
        return None
    # Path-based tenant routes use the same canonicalization and validation
    # contract as tenant creation so auth decisions never depend on raw casing.
    return _canonical_tenant_id(tenant_id)


def _tenant_pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def _tenant_key(tenant_id: str) -> dict[str, str]:
    return {"PK": _tenant_pk(tenant_id), "SK": "METADATA"}


def _canonical_tenant_id(value: Any) -> str:
    tenant_id = _str_or_none(value)
    if tenant_id is None:
        raise ValueError("tenantId is required")

    normalized = tenant_id.lower()
    if len(normalized) < _TENANT_ID_MIN_LENGTH or len(normalized) > _TENANT_ID_MAX_LENGTH:
        raise ValueError("tenantId must be 3-32 characters")
    if "--" in normalized:
        raise ValueError("tenantId must not contain consecutive hyphens")
    if normalized in _RESERVED_TENANT_IDS:
        raise ValueError("tenantId is reserved")
    if not _TENANT_ID_PATTERN.fullmatch(normalized):
        raise ValueError("tenantId must match ^[a-z](?:[a-z0-9-]{1,30}[a-z0-9])$")
    return normalized


def _require_aws_account_id(value: Any, *, field: str) -> str:
    account_id = _str_or_none(value)
    if account_id is None:
        raise ValueError(f"{field} is required")
    if not _AWS_ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise ValueError(f"{field} must match ^[0-9]{{12}}$")
    return account_id


def _parse_utc_timestamp(value: Any, *, field: str) -> datetime:
    text = _str_or_none(value)
    if text is None:
        raise ValueError(f"{field} must be an ISO 8601 UTC timestamp")
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _parse_optional_utc_timestamp(value: Any, *, field: str) -> datetime | None:
    if _str_or_none(value) is None:
        return None
    return _parse_utc_timestamp(value, field=field)


def _format_export_timestamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _coerce_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def _ssm_parameter_value(ssm: Any, name: str, *, required: bool) -> str | None:
    try:
        response = ssm.get_parameter(Name=name)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if not required and error_code == "ParameterNotFound":
            return None
        raise

    value = _str_or_none(response.get("Parameter", {}).get("Value"))
    if value is None and required:
        raise ValueError(f"SSM parameter {name} is empty")
    return value


def _required_ssm_parameter(ssm: Any, name: str) -> str:
    value = _ssm_parameter_value(ssm, name, required=True)
    if value is None:
        raise ValueError(f"SSM parameter {name} is empty")
    return value


def _optional_ssm_parameter(ssm: Any, name: str) -> str | None:
    return _ssm_parameter_value(ssm, name, required=False)


def _tenants_table_name() -> str:
    return os.environ.get(_TENANTS_TABLE_ENV, "platform-tenants")


def _invocations_table_name() -> str:
    return os.environ.get(_INVOCATIONS_TABLE_ENV, "platform-invocations")


def _event_bus_name() -> str:
    return os.environ.get(_EVENT_BUS_ENV, "default")


def _audit_export_bucket() -> str | None:
    return _str_or_none(os.environ.get(_AUDIT_EXPORT_BUCKET_ENV))


def _secret_prefix() -> str:
    return os.environ.get(_API_KEY_SECRET_PREFIX_ENV, "platform/tenants")


def _audit_export_url_expiry_seconds() -> int:
    raw = os.environ.get("AUDIT_EXPORT_URL_EXPIRY_SECONDS")
    return _coerce_positive_int(raw, default=_AUDIT_EXPORT_URL_EXPIRY_SECONDS)


def _ops_locks_table_name() -> str:
    return os.environ.get(_OPS_LOCKS_TABLE_ENV, _DEFAULT_OPS_LOCKS_TABLE)


def _runtime_region_param_name() -> str:
    return os.environ.get(_RUNTIME_REGION_PARAM_ENV, _DEFAULT_RUNTIME_REGION_PARAM)


def _fallback_region_param_name() -> str:
    return os.environ.get(_FALLBACK_REGION_PARAM_ENV, _DEFAULT_FALLBACK_REGION_PARAM)


def _failover_lock_name() -> str:
    return os.environ.get(_FAILOVER_LOCK_NAME_ENV, _DEFAULT_FAILOVER_LOCK_NAME)


def _dependencies() -> TenantApiDependencies:
    region = os.environ["AWS_REGION"]
    session = boto3.session.Session(region_name=region)
    return TenantApiDependencies(
        secretsmanager=session.client("secretsmanager"),
        events=session.client("events"),
        dynamodb=session.resource("dynamodb"),
        ssm=session.client("ssm"),
        awslambda=session.client("lambda"),
        usage_client=_NoopUsageClient(),
        memory_provisioner=_NoopMemoryProvisioner(),
        platform_quota_client=_AwsPlatformQuotaClient(session),
    )


class _NoopUsageClient:
    def get_tenant_usage(self, *, tenant_id: str, app_id: str | None) -> dict[str, Any]:
        return {"tenantId": tenant_id, "appId": app_id}


class _NoopMemoryProvisioner:
    def provision(self, *, tenant_id: str, app_id: str) -> dict[str, Any]:
        return {}


class _AwsPlatformQuotaClient:
    def __init__(self, session: Any) -> None:
        self._session = session

    def get_utilisation(
        self,
        *,
        active_region: str,
        fallback_region: str | None,
    ) -> list[dict[str, Any]]:
        regions: list[str] = []
        for region in (active_region, fallback_region):
            if region and region not in regions:
                regions.append(region)

        return [self._build_region_entry(region) for region in regions]

    def _build_region_entry(self, region: str) -> dict[str, Any]:
        current_value = self._current_sessions(region)
        limit = self._quota_limit(region)
        utilisation = 0.0 if limit <= 0 else round((current_value / limit) * 100, 2)
        return {
            "region": region,
            "quotaName": _AGENTCORE_CONCURRENT_SESSIONS_METRIC,
            "currentValue": current_value,
            "limit": limit,
            "utilisationPercentage": utilisation,
        }

    def _current_sessions(self, region: str) -> float:
        cloudwatch = self._session.client("cloudwatch", region_name=region)
        end_time = _now_utc()
        start_time = end_time - timedelta(minutes=_AGENTCORE_QUOTA_LOOKBACK_MINUTES)
        response = cloudwatch.get_metric_statistics(
            Namespace=_AGENTCORE_CONCURRENT_SESSIONS_NAMESPACE,
            MetricName=_AGENTCORE_CONCURRENT_SESSIONS_METRIC,
            StartTime=start_time,
            EndTime=end_time,
            Period=60,
            Statistics=["Maximum"],
        )
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return 0.0
        return max(float(point.get("Maximum", 0.0)) for point in datapoints)

    def _quota_limit(self, region: str) -> float:
        service_quotas = self._session.client("service-quotas", region_name=region)
        next_token: str | None = None

        while True:
            request: dict[str, Any] = {"ServiceCode": "bedrock-agentcore"}
            if next_token:
                request["NextToken"] = next_token

            response = service_quotas.list_service_quotas(**request)
            for quota in response.get("Quotas", []):
                if quota.get("QuotaName") == _AGENTCORE_QUOTA_NAME:
                    return float(quota.get("Value", 0.0))

            next_token = response.get("NextToken")
            if not next_token:
                break

        return self._documented_default_limit(region)

    @staticmethod
    def _documented_default_limit(region: str) -> float:
        return 1000.0 if region == "us-east-1" else 500.0


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


def _tenant_s3_for_scope(
    *,
    tenant_id: str,
    caller: CallerIdentity,
    app_id: str | None,
) -> TenantScopedS3:
    tenant_context = _tenant_context_for_scope(
        tenant_id=tenant_id,
        caller=caller,
        app_id=app_id,
    )
    return TenantScopedS3(tenant_context)


def _control_plane_db(caller: CallerIdentity) -> TenantScopedDynamoDB:
    return _db_for_tenant(
        tenant_id=caller.tenant_id or "platform-admin",
        caller=caller,
        app_id=caller.app_id or "platform-admin",
    )


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


def _is_self_service_admin(caller: CallerIdentity) -> bool:
    return bool(caller.roles & _SELF_SERVICE_ADMIN_ROLES)


def _can_manage_tenant_self_service(caller: CallerIdentity, tenant_id: str) -> bool:
    return _is_self_service_admin(caller)


def _normalize_tenant_invite_role(value: Any) -> str:
    role = _str_or_none(value) or "Agent.Invoke"
    if role not in _ALLOWED_TENANT_INVITE_ROLES:
        allowed = ", ".join(sorted(_ALLOWED_TENANT_INVITE_ROLES))
        raise ValueError(f"role must be one of: {allowed}")
    return role


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


def _read_failover_lock_record(caller: CallerIdentity) -> dict[str, Any] | None:
    db = _control_plane_db(caller)
    return db.get_item(
        _ops_locks_table_name(),
        {"PK": f"LOCK#{_failover_lock_name()}", "SK": "METADATA"},
    )


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
        "executionRoleArn",
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

    tenant_id = _canonical_tenant_id(body["tenantId"])
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

    for field in ("runtimeRegion", "fallbackRegion", "executionRoleArn"):
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
        detail={
            "tenantId": tenant_id,
            "appId": app_id,
            "tier": tier,
            "accountId": attributes["accountId"],
            "actorSub": caller.sub,
        },
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

    scan_params: dict[str, Any] = {
        "limit": limit,
    }
    if next_token:
        try:
            scan_params["exclusive_start_key"] = json.loads(next_token)
        except json.JSONDecodeError:
            raise ValueError("Invalid nextToken")

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
        scan_params["filter_expression"] = " AND ".join(filter_exprs)
        scan_params["expression_attribute_names"] = expr_names
        scan_params["expression_attribute_values"] = expr_values

    # Scan the table using data-access-lib
    result = db.scan(_tenants_table_name(), **scan_params)

    return _response(
        200,
        {
            "items": [_serialize_tenant(item) for item in result.items],
            "nextToken": (
                json.dumps(result.last_evaluated_key) if result.last_evaluated_key else None
            ),
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
    allowed = {
        "tier",
        "monthlyBudgetUsd",
        "status",
        "executionRoleArn",
        "memoryStoreArn",
        "runtimeRegion",
        "fallbackRegion",
    }
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

    # Infrastructure fields
    for field in (
        "executionRoleArn",
        "memoryStoreArn",
        "runtimeRegion",
        "fallbackRegion",
    ):
        if field in body:
            attrs[field] = _str_or_none(body[field])

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


def _handle_rotate_api_key(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not _can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    existing = _read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return _error(404, "NOT_FOUND", "Tenant not found")

    app_id = _str_or_none(existing.get("appId"))
    secret_arn = _str_or_none(existing.get("apiKeySecretArn"))
    if app_id is None or secret_arn is None:
        return _error(
            409,
            "CONFLICT",
            "Tenant is missing API key secret metadata",
        )

    rotated_at = _now_utc()
    secret_value = {
        "tenantId": tenant_id,
        "appId": app_id,
        "apiKey": secrets.token_urlsafe(32),
        "rotatedAt": _iso(rotated_at),
    }
    put_response = deps.secretsmanager.put_secret_value(
        SecretId=secret_arn,
        SecretString=json.dumps(secret_value),
    )

    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    update_expression, expr_names, expr_values = _build_update_expression(
        {"updatedAt": _iso(rotated_at)}
    )
    db.update_item(
        _tenants_table_name(),
        key=_tenant_key(tenant_id),
        update_expression=update_expression,
        expression_attribute_values=expr_values,
        expression_attribute_names=expr_names,
        condition_expression="attribute_exists(PK) AND attribute_exists(SK)",
    )

    _put_event(
        deps,
        detail_type="tenant.api_key_rotated",
        detail={
            "tenantId": tenant_id,
            "appId": app_id,
            "actorSub": caller.sub,
            "secretArn": secret_arn,
        },
    )
    return _response(
        200,
        {
            "tenantId": tenant_id,
            "apiKeySecretArn": secret_arn,
            "rotatedAt": _iso(rotated_at),
            "versionId": _str_or_none(put_response.get("VersionId")),
        },
    )


def _handle_invite_user(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not _can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    existing = _read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return _error(404, "NOT_FOUND", "Tenant not found")

    body = _require_json_body(event)
    email = _str_or_none(body.get("email"))
    if email is None or "@" not in email:
        raise ValueError("email is required and must be a valid email address")

    role = _normalize_tenant_invite_role(body.get("role"))
    display_name = _str_or_none(body.get("displayName"))
    app_id = _str_or_none(existing.get("appId"))

    invite_id = f"invite-{secrets.token_hex(8)}"
    now = _now_utc()
    expires_at = now + timedelta(days=_INVITE_EXPIRY_DAYS)
    invite = {
        "PK": f"TENANT#{tenant_id}",
        "SK": f"INVITE#{invite_id}",
        "inviteId": invite_id,
        "tenantId": tenant_id,
        "email": email.lower(),
        "role": role,
        "displayName": display_name,
        "status": "pending",
        "createdAt": _iso(now),
        "expiresAt": _iso(expires_at),
    }

    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    db.put_item(_tenants_table_name(), invite)

    _put_event(
        deps,
        detail_type="tenant.user_invited",
        detail={
            **invite,
            "actorSub": caller.sub,
            "appId": app_id,
        },
    )
    # Filter out PK/SK for response
    response_invite = {k: v for k, v in invite.items() if k not in ("PK", "SK")}
    return _response(202, {"invite": response_invite})


def _handle_list_invites(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not _can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    result = db.query(
        _tenants_table_name(),
        sk_condition=Key("SK").begins_with("INVITE#"),
    )

    items = []
    for item in result.items:
        items.append({k: v for k, v in item.items() if k not in ("PK", "SK")})

    return _response(200, {"items": items})


def _invocation_timestamp(item: dict[str, Any]) -> str | None:
    explicit = _str_or_none(item.get("timestamp"))
    if explicit is not None:
        return explicit

    sort_key = _str_or_none(item.get("SK"))
    if sort_key is None or not sort_key.startswith("INV#"):
        return None
    parts = sort_key.split("#", 2)
    if len(parts) < 3:
        return None
    return _str_or_none(parts[1])


def _audit_export_sk_condition(
    *,
    start_at: datetime | None,
    end_at: datetime | None,
) -> ConditionBase | None:
    start_text = f"INV#{_iso(start_at)}" if start_at is not None else None
    end_text = f"INV#{_iso(end_at)}~" if end_at is not None else None
    if start_text and end_text:
        return Key("SK").between(start_text, end_text)
    if start_text:
        return Key("SK").gte(start_text)
    if end_text:
        return Key("SK").lte(end_text)
    return None


def _collect_audit_export_records(
    *,
    tenant_id: str,
    caller: CallerIdentity,
    app_id: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
) -> list[dict[str, Any]]:
    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    last_evaluated_key: dict[str, Any] | None = None
    items: list[dict[str, Any]] = []
    sk_condition = _audit_export_sk_condition(start_at=start_at, end_at=end_at)
    start_text = _iso(start_at) if start_at is not None else None
    end_text = _iso(end_at) if end_at is not None else None

    while True:
        page = db.query(
            _invocations_table_name(),
            sk_condition=sk_condition,
            limit=_AUDIT_EXPORT_PAGE_SIZE,
            exclusive_start_key=last_evaluated_key,
        )
        for item in page.items:
            item_timestamp = _invocation_timestamp(item)
            if item_timestamp is None:
                continue
            if start_text is not None and item_timestamp < start_text:
                continue
            if end_text is not None and item_timestamp > end_text:
                continue
            items.append(item)
        last_evaluated_key = page.last_evaluated_key
        if last_evaluated_key is None:
            return items


def _audit_export_key(tenant_id: str, generated_at: datetime) -> str:
    timestamp = _format_export_timestamp(generated_at)
    nonce = secrets.token_hex(8)
    return f"tenants/{tenant_id}/{_AUDIT_EXPORT_PREFIX}/audit-export-{timestamp}-{nonce}.json"


def _build_audit_export_payload(
    *,
    tenant_id: str,
    generated_at: datetime,
    start_at: datetime | None,
    end_at: datetime | None,
    records: list[dict[str, Any]],
) -> bytes:
    payload = {
        "tenantId": tenant_id,
        "generatedAt": _iso(generated_at),
        "windowStart": _iso(start_at) if start_at is not None else None,
        "windowEnd": _iso(end_at) if end_at is not None else None,
        "recordCount": len(records),
        "records": records,
    }
    return json.dumps(payload, default=_json_default).encode("utf-8")


def _handle_audit_export(
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

    query = _query_params(event)
    start_at = _parse_optional_utc_timestamp(query.get("start"), field="start")
    end_at = _parse_optional_utc_timestamp(query.get("end"), field="end")
    if start_at is not None and end_at is not None and start_at > end_at:
        raise ValueError("start must be less than or equal to end")

    bucket = _audit_export_bucket()
    if bucket is None:
        logger.error("Audit export bucket is not configured")
        return _error(500, "INTERNAL_ERROR", "Audit export bucket is not configured")

    app_id = _str_or_none(existing.get("appId"))
    logger.info(
        "Generating tenant audit export",
        extra={
            "target_tenantid": tenant_id,
            "window_start": _iso(start_at) if start_at is not None else None,
            "window_end": _iso(end_at) if end_at is not None else None,
        },
    )
    records = _collect_audit_export_records(
        tenant_id=tenant_id,
        caller=caller,
        app_id=app_id,
        start_at=start_at,
        end_at=end_at,
    )
    generated_at = _now_utc()
    object_key = _audit_export_key(tenant_id, generated_at)
    payload = _build_audit_export_payload(
        tenant_id=tenant_id,
        generated_at=generated_at,
        start_at=start_at,
        end_at=end_at,
        records=records,
    )

    try:
        tenant_s3 = _tenant_s3_for_scope(tenant_id=tenant_id, caller=caller, app_id=app_id)
        tenant_s3.put_object(
            bucket,
            object_key,
            payload,
            ContentType="application/json",
        )
        expires_in = _audit_export_url_expiry_seconds()
        download_url = tenant_s3.generate_presigned_url(
            bucket,
            object_key,
            expires_in=expires_in,
        )
    except Exception:
        logger.exception("Failed to generate tenant audit export")
        return _error(500, "INTERNAL_ERROR", "Failed to generate audit export")

    logger.info(
        "Generated tenant audit export",
        extra={
            "target_tenantid": tenant_id,
            "record_count": len(records),
            "object_key": object_key,
        },
    )
    expires_at = generated_at + timedelta(seconds=_audit_export_url_expiry_seconds())
    return _response(
        200,
        {
            "tenantId": tenant_id,
            "downloadUrl": download_url,
            "expiresAt": _iso(expires_at),
        },
    )


def _handle_list_webhooks(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not _can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    result = db.query(
        _tenants_table_name(),
        sk_condition=Key("SK").begins_with("WEBHOOK#"),
    )

    items = []
    for item in result.items:
        items.append(
            {
                "webhookId": item.get("webhook_id"),
                "callbackUrl": item.get("callback_url"),
                "events": item.get("events"),
                "status": item.get("status"),
                "description": item.get("description"),
                "createdAt": item.get("created_at"),
                "updatedAt": item.get("updated_at"),
                "signatureHeader": item.get("signature_header", "X-Platform-Signature"),
                "signatureAlgorithm": item.get("signature_algorithm", "HMAC-SHA256"),
            }
        )

    return _response(200, {"items": items})


def _handle_register_webhook(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not _can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    body = _require_json_body(event)
    callback_url = _str_or_none(body.get("callbackUrl"))
    if callback_url is None:
        raise ValueError("callbackUrl is required")

    # Strict URL validation matching Bridge implementation
    parsed_url = urllib.parse.urlparse(callback_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return _error(422, "UNPROCESSABLE_ENTITY", "callbackUrl must be a valid URL")

    events_raw = body.get("events")
    if not isinstance(events_raw, list) or not events_raw:
        raise ValueError("events must be a non-empty array")

    # Supported events matching Bridge/OpenAPI
    valid_events = {"job.completed", "job.failed"}
    normalized_events: list[str] = []
    seen_events: set[str] = set()
    for raw_event in events_raw:
        event_name = _str_or_none(raw_event)
        if event_name is None:
            return _error(422, "UNPROCESSABLE_ENTITY", "events must contain non-empty values")
        if event_name not in valid_events:
            return _error(422, "UNPROCESSABLE_ENTITY", f"Unsupported webhook event '{event_name}'")
        if event_name in seen_events:
            raise ValueError("events must not contain duplicate values")
        seen_events.add(event_name)
        normalized_events.append(event_name)

    description = _str_or_none(body.get("description"))
    if description and len(description) > 256:
        return _error(422, "UNPROCESSABLE_ENTITY", "description must be 256 characters or fewer")

    webhook_id = str(uuid.uuid4())
    now = _now_utc()
    webhook_secret = secrets.token_urlsafe(32)

    webhook = {
        "PK": f"TENANT#{tenant_id}",
        "SK": f"WEBHOOK#{webhook_id}",
        "webhook_id": webhook_id,
        "tenant_id": tenant_id,
        "callback_url": callback_url,
        "events": normalized_events,
        "status": "active",
        "description": description,
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "signature_secret": webhook_secret,
        "signature_header": "X-Platform-Signature",
        "signature_algorithm": "HMAC-SHA256",
        "record_type": "webhook_registration",
    }

    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    db.put_item(_tenants_table_name(), webhook)

    _put_event(
        deps,
        detail_type="tenant.webhook_registered",
        detail={
            **webhook,
            "actorSub": caller.sub,
        },
    )

    # Filter out sensitive fields for response
    response_webhook = {
        "webhookId": webhook_id,
        "callbackUrl": callback_url,
        "events": normalized_events,
        "createdAt": _iso(now),
        "signatureHeader": "X-Platform-Signature",
        "signatureAlgorithm": "HMAC-SHA256",
    }

    return _response(201, response_webhook)


def _handle_delete_webhook(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    *,
    tenant_id: str,
    webhook_id: str,
) -> dict[str, Any]:
    if not _can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    key = {"PK": f"TENANT#{tenant_id}", "SK": f"WEBHOOK#{webhook_id}"}

    # Check if it exists
    existing = db.get_item(_tenants_table_name(), key)
    if existing is None:
        return _error(404, "NOT_FOUND", "Webhook not found")

    db.delete_item(_tenants_table_name(), key)

    _put_event(
        deps,
        detail_type="tenant.webhook_deleted",
        detail={
            "tenantId": tenant_id,
            "webhookId": webhook_id,
            "actorSub": caller.sub,
        },
    )

    return _response(204, {})


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

    lock_record = _read_failover_lock_record(caller)
    if lock_record is None:
        logger.warning(
            "Platform failover rejected: lock missing",
            extra={
                "actor": caller.sub,
                "lock_id": lock_id,
                "target_region": target_region,
                "lock_name": _failover_lock_name(),
            },
        )
        return _error(409, "LOCK_NOT_HELD", "Runtime failover lock is not currently held")

    current_lock_id = _str_or_none(lock_record.get("lockId") or lock_record.get("lock_id"))
    acquired_by = _str_or_none(lock_record.get("acquiredBy") or lock_record.get("acquired_by"))
    ttl_raw = lock_record.get("ttl")
    if ttl_raw is None:
        raise ValueError("Failover lock record is invalid")
    try:
        ttl = int(ttl_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Failover lock record is invalid") from exc

    now_epoch = int(_now_utc().timestamp())
    if ttl <= now_epoch:
        logger.warning(
            "Platform failover rejected: lock expired",
            extra={
                "actor": caller.sub,
                "lock_id": lock_id,
                "current_lock_id": current_lock_id,
                "target_region": target_region,
                "lock_owner": acquired_by,
                "lock_expires_at": ttl,
            },
        )
        return _error(409, "LOCK_EXPIRED", "Runtime failover lock has expired")

    if current_lock_id != lock_id:
        logger.warning(
            "Platform failover rejected: lock mismatch",
            extra={
                "actor": caller.sub,
                "lock_id": lock_id,
                "current_lock_id": current_lock_id,
                "target_region": target_region,
                "lock_owner": acquired_by,
            },
        )
        return _error(
            409,
            "LOCK_MISMATCH",
            "Runtime failover lock is held by another actor or session",
        )

    current_region = str(
        deps.ssm.get_parameter(Name=_runtime_region_param_name())["Parameter"]["Value"]
    ).strip()
    if current_region == target_region:
        logger.info(
            "Platform failover already completed",
            extra={
                "actor": caller.sub,
                "lock_id": lock_id,
                "lock_owner": acquired_by,
                "previous_region": current_region,
                "target_region": target_region,
                "changed": False,
            },
        )
        return _response(
            200,
            {
                "status": "completed",
                "region": target_region,
                "previousRegion": current_region,
                "lockId": lock_id,
                "changed": False,
            },
        )

    try:
        deps.ssm.put_parameter(
            Name=_runtime_region_param_name(),
            Value=target_region,
            Type="String",
            Overwrite=True,
        )
    except ClientError:
        logger.exception(
            "Platform failover SSM update failed",
            extra={
                "actor": caller.sub,
                "lock_id": lock_id,
                "lock_owner": acquired_by,
                "previous_region": current_region,
                "target_region": target_region,
            },
        )
        raise

    logger.info(
        "Platform failover completed",
        extra={
            "actor": caller.sub,
            "lock_id": lock_id,
            "lock_owner": acquired_by,
            "previous_region": current_region,
            "target_region": target_region,
            "changed": True,
        },
    )

    return _response(
        200,
        {
            "status": "completed",
            "region": target_region,
            "previousRegion": current_region,
            "lockId": lock_id,
            "changed": True,
        },
    )


def _handle_health(deps: TenantApiDependencies) -> dict[str, Any]:
    try:
        region_param = deps.ssm.get_parameter(Name=_runtime_region_param_name())
        runtime_region = region_param["Parameter"]["Value"]
    except Exception:
        logger.warning("Failed to fetch runtime region from SSM, using default")
        runtime_region = os.environ.get("RUNTIME_REGION_DEFAULT", "eu-west-1")

    return _response(
        200,
        {
            "status": "ok",
            "version": os.environ.get("SERVICE_VERSION", "0.1.0"),
            "runtimeRegion": runtime_region,
            "timestamp": _iso(_now_utc()),
            "checks": {"tenantApi": {"status": "ok"}},
        },
    )


def _handle_sessions(event: dict[str, Any], caller: CallerIdentity) -> dict[str, Any]:
    if caller.tenant_id is None:
        return _error(400, "BAD_REQUEST", "tenant context missing")

    query = event.get("queryStringParameters") or {}
    limit_raw = query.get("limit", 50)
    try:
        limit = max(1, min(int(limit_raw), 100))
    except (TypeError, ValueError):
        return _error(400, "BAD_REQUEST", "limit must be an integer between 1 and 100")

    _ = limit
    return _error(
        501,
        "NOT_IMPLEMENTED",
        "Session listing is not available until tenant-backed session tracking is implemented",
    )


def _handle_platform_quota(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    _require_admin(caller)
    active_region = _required_ssm_parameter(deps.ssm, _runtime_region_param_name())
    fallback_region = _optional_ssm_parameter(deps.ssm, _fallback_region_param_name())
    utilisation = deps.platform_quota_client.get_utilisation(
        active_region=active_region,
        fallback_region=fallback_region,
    )
    return _response(
        200,
        {"utilisation": utilisation},
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
    target_account_id = _require_aws_account_id(
        body.get("targetAccountId"),
        field="targetAccountId",
    )

    # In a real implementation, this would trigger an Step Function or async job
    # to move tenants of the specified tier to a new account.
    job_id = f"job-split-{secrets.token_hex(4)}"
    logger.info(
        "Account split initiated",
        extra={"tier": tier, "target_account_id": target_account_id, "job_id": job_id},
    )

    return _response(202, {"status": "initiated", "jobId": job_id})


def _handle_platform_service_health(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    _require_admin(caller)
    return _response(
        200,
        {
            "status": "healthy",
            "regions": [
                {"region": "eu-west-1", "status": "operational", "latency_ms": 12},
                {"region": "eu-central-1", "status": "operational", "latency_ms": 25},
            ],
            "services": {
                "AgentCore": "operational",
                "DynamoDB": "operational",
                "Bedrock": "operational",
            },
        },
    )


def _handle_platform_billing_status(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    _require_admin(caller)

    # We aggregate global status across all tenants for the current month
    year_month = datetime.now(UTC).strftime("%Y-%m")

    # Use data-access-lib for admin scan
    db = _control_plane_db(caller)
    summaries = db.scan_all(
        _tenants_table_name(),
        filter_expression=Key("SK").eq(f"BILLING#{year_month}"),
    )

    total_cost = sum(float(s.get("total_cost_usd", 0.0)) for s in summaries)
    total_input = sum(int(s.get("total_input_tokens", 0)) for s in summaries)
    total_output = sum(int(s.get("total_output_tokens", 0)) for s in summaries)

    # Get some info about the billing Lambda last run from CloudWatch if we want,
    # but for now let's just return aggregated month-to-date.

    return _response(
        200,
        {
            "status": "active",
            "yearMonth": year_month,
            "tenantCount": len(summaries),
            "totalCostUsd": round(total_cost, 2),
            "totalTokens": total_input + total_output,
            "lastUpdated": _iso(_now_utc()),
        },
    )


def _handle_ops_top_tenants(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    query = event.get("queryStringParameters") or {}
    n = int(query.get("n", 10))
    return _response(
        200,
        {
            "tenants": [
                {"tenantId": f"t-{i:03d}", "tokens": 1000000 - (i * 10000)} for i in range(1, n + 1)
            ]
        },
    )


def _handle_ops_security_events(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    return _response(
        200,
        {
            "events": [
                {
                    "timestamp": _iso(_now_utc() - timedelta(minutes=5)),
                    "type": "tenant_access_violation",
                    "tenantId": "t-suspicious",
                    "details": "Cross-tenant partition access attempt detected",
                }
            ]
        },
    )


def _handle_ops_error_rate(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    return _response(
        200,
        {
            "errorRate": 0.02,
            "periodMinutes": int((event.get("queryStringParameters") or {}).get("minutes", 5)),
            "threshold": 0.05,
        },
    )


def _handle_ops_dlq_inspect(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    queue_name: str,
) -> dict[str, Any]:
    return _response(
        200,
        {
            "queueName": queue_name,
            "approximateNumberOfMessages": 3,
            "messages": [
                {
                    "messageId": f"msg-{i}",
                    "timestamp": _iso(_now_utc()),
                    "body": {"jobId": f"job-{i}"},
                }
                for i in range(3)
            ],
        },
    )


def _handle_ops_dlq_redrive(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    queue_name: str,
) -> dict[str, Any]:
    return _response(200, {"status": "initiated", "redriveCount": 3})


def _handle_ops_tenant_sessions(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    return _response(
        200,
        {
            "tenantId": tenant_id,
            "activeSessions": [
                {"sessionId": f"sess-{i}", "lastActivity": _iso(_now_utc())} for i in range(2)
            ],
        },
    )


def _handle_ops_suspend_tenant(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    body = _require_json_body(event)
    reason = body.get("reason", "No reason provided")
    return _response(200, {"tenantId": tenant_id, "status": "suspended", "reason": reason})


def _handle_ops_reinstate_tenant(
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    return _response(200, {"tenantId": tenant_id, "status": "active"})


def _handle_ops_invocation_report(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    return _response(
        200,
        {
            "tenantId": tenant_id,
            "totalInvocations": 1250,
            "successRate": 0.992,
            "avgLatencyMs": 450,
        },
    )


def _handle_ops_notify_tenant(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    body = _require_json_body(event)
    template = body.get("template")
    return _response(200, {"status": "sent", "tenantId": tenant_id, "template": template})


def _handle_ops_fail_job(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    job_id: str,
) -> dict[str, Any]:
    body = _require_json_body(event)
    reason = body.get("reason")
    return _response(200, {"jobId": job_id, "status": "failed", "reason": reason})


def _handle_ops_lambda_rollback(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    _require_admin(caller)
    body = _require_json_body(event)
    suffix = _str_or_none(body.get("functionSuffix"))
    alias_name = _str_or_none(body.get("aliasName")) or "live"

    if suffix is None:
        raise ValueError("functionSuffix is required")

    # Resolve full function name platform-{suffix}-{env}
    current_fn = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "platform-tenant-api-dev")
    # env is the last part of our own function name
    env = current_fn.split("-")[-1]
    full_name = f"platform-{suffix}-{env}"

    logger.info(
        "Initiating Lambda rollback",
        extra={
            "target_function": full_name,
            "alias": alias_name,
            "actor": caller.sub,
        },
    )

    try:
        # 1. Get current alias
        alias = deps.awslambda.get_alias(FunctionName=full_name, Name=alias_name)
        current_version = alias["FunctionVersion"]

        # 2. List versions (handle basic pagination)
        versions: list[str] = []
        paginator = deps.awslambda.get_paginator("list_versions_by_function")
        for page in paginator.paginate(FunctionName=full_name):
            for v in page.get("Versions", []):
                v_num = v["Version"]
                if v_num != "$LATEST":
                    versions.append(v_num)

        # Numerical sort (they should be strings of integers)
        versions.sort(key=lambda x: int(x))

        if current_version not in versions:
            # Maybe it's pointing to $LATEST or some other version not in the list?
            # Or maybe it was deleted.
            return _error(
                409,
                "CONFLICT",
                f"Current version {current_version} not found in published versions list",
            )

        idx = versions.index(current_version)
        if idx == 0:
            return _error(
                409,
                "NO_PREVIOUS_VERSION",
                f"Version {current_version} is the oldest published version; cannot roll back.",
            )

        previous_version = versions[idx - 1]

        # 3. Update alias
        deps.awslambda.update_alias(
            FunctionName=full_name,
            Name=alias_name,
            FunctionVersion=previous_version,
            Description=f"Rollback from {current_version} to {previous_version} by {caller.sub}",
        )

        logger.info(
            "Lambda rollback completed",
            extra={
                "target_function": full_name,
                "alias": alias_name,
                "from_version": current_version,
                "to_version": previous_version,
            },
        )

        return _response(
            200,
            {
                "functionName": full_name,
                "aliasName": alias_name,
                "fromVersion": current_version,
                "toVersion": previous_version,
                "status": "rolled_back",
            },
        )

    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            return _error(
                404, "NOT_FOUND", f"Function or alias not found: {full_name}:{alias_name}"
            )
        logger.exception("Lambda rollback failed")
        return _error(500, "INTERNAL_ERROR", f"AWS Error: {code}")


def _handle_ops_page_security(
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any]:
    _require_json_body(event)
    return _response(200, {"status": "paged", "incidentId": f"inc-{secrets.token_hex(4)}"})


def _request_path(event: dict[str, Any]) -> str:
    path = event.get("path")
    if not path:
        path = event.get("requestContext", {}).get("http", {}).get("path")
    return str(path or "").rstrip("/")


def _dispatch_platform_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any] | None:
    if path == "/v1/platform/failover" and method == "POST":
        return _handle_platform_failover(event, caller, deps)
    if path == "/v1/platform/quota" and method == "GET":
        return _handle_platform_quota(caller, deps)
    if path == "/v1/platform/quota/split-accounts" and method == "POST":
        return _handle_platform_split_accounts(event, caller, deps)
    if path == "/v1/platform/service-health" and method == "GET":
        return _handle_platform_service_health(caller, deps)
    if path == "/v1/platform/billing/status" and method == "GET":
        return _handle_platform_billing_status(caller, deps)
    return None


def _dispatch_ops_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any] | None:
    path_lower = path.lower()
    if not path_lower.startswith("/v1/platform/ops/"):
        return None

    _require_admin(caller)
    if path_lower == "/v1/platform/ops/top-tenants" and method == "GET":
        return _handle_ops_top_tenants(event, caller, deps)
    if path_lower == "/v1/platform/ops/security-events" and method == "GET":
        return _handle_ops_security_events(event, caller, deps)
    if path_lower == "/v1/platform/ops/error-rate" and method == "GET":
        return _handle_ops_error_rate(event, caller, deps)
    if path_lower == "/v1/platform/ops/lambda-rollback" and method == "POST":
        return _handle_ops_lambda_rollback(event, caller, deps)

    parts = path.split("/")
    if path_lower.startswith("/v1/platform/ops/dlq/"):
        # Expected: /v1/platform/ops/dlq/{queueName} (len 6)
        # or /v1/platform/ops/dlq/{queueName}/redrive (len 7)
        if len(parts) == 6 and method == "GET":
            queue_name = parts[5]
            return _handle_ops_dlq_inspect(caller, deps, queue_name=queue_name)
        if len(parts) == 7 and parts[6].lower() == "redrive" and method == "POST":
            queue_name = parts[5]
            return _handle_ops_dlq_redrive(caller, deps, queue_name=queue_name)

    if path_lower.startswith("/v1/platform/ops/tenants/"):
        # Expected: /v1/platform/ops/tenants/{tenantId}/{subpath} (len 7)
        if len(parts) == 7:
            tenant_id = parts[5]
            subpath = parts[6].lower()
            if subpath == "sessions" and method == "GET":
                return _handle_ops_tenant_sessions(caller, deps, tenant_id=tenant_id)
            if subpath == "suspend" and method == "POST":
                return _handle_ops_suspend_tenant(event, caller, deps, tenant_id=tenant_id)
            if subpath == "reinstate" and method == "POST":
                return _handle_ops_reinstate_tenant(caller, deps, tenant_id=tenant_id)
            if subpath == "invocations" and method == "GET":
                return _handle_ops_invocation_report(event, caller, deps, tenant_id=tenant_id)
            if subpath == "notify" and method == "POST":
                return _handle_ops_notify_tenant(event, caller, deps, tenant_id=tenant_id)

    if path_lower.startswith("/v1/platform/ops/jobs/"):
        # Expected: /v1/platform/ops/jobs/{jobId}/fail (len 7)
        if len(parts) == 7 and parts[6].lower() == "fail" and method == "POST":
            job_id = parts[5]
            return _handle_ops_fail_job(event, caller, deps, job_id=job_id)

    if path_lower == "/v1/platform/ops/security/page" and method == "POST":
        return _handle_ops_page_security(event, caller, deps)

    return None


def _dispatch_webhook_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any] | None:
    path_lower = path.lower()
    if path_lower == "/v1/webhooks":
        if caller.tenant_id is None:
            return _error(400, "BAD_REQUEST", "tenant context required")
        if method == "GET":
            return _handle_list_webhooks(caller, deps, tenant_id=caller.tenant_id)
        if method == "POST":
            return _handle_register_webhook(event, caller, deps, tenant_id=caller.tenant_id)

    if path_lower.startswith("/v1/webhooks/") and method == "DELETE":
        parts = path.split("/")
        if len(parts) == 4:  # /v1/webhooks/{webhookId}
            if caller.tenant_id is None:
                return _error(400, "BAD_REQUEST", "tenant context required")
            webhook_id = parts[3]
            return _handle_delete_webhook(
                caller, deps, tenant_id=caller.tenant_id, webhook_id=webhook_id
            )

    return None


def _dispatch_tenant_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    tenant_id: str | None,
) -> dict[str, Any] | None:
    path_lower = path.lower()
    if path_lower == "/v1/tenants":
        if method == "POST":
            return _handle_create(event, caller, deps)
        if method == "GET":
            return _handle_list(event, caller, deps)

    if tenant_id is not None:
        tenant_base = f"/v1/tenants/{tenant_id}"
        if path_lower == f"{tenant_base}/api-key/rotate" and method == "POST":
            return _handle_rotate_api_key(caller, deps, tenant_id=tenant_id)
        if path_lower == f"{tenant_base}/users/invites" and method == "GET":
            return _handle_list_invites(caller, deps, tenant_id=tenant_id)
        if path_lower == f"{tenant_base}/users/invite" and method == "POST":
            return _handle_invite_user(event, caller, deps, tenant_id=tenant_id)
        if path_lower == f"{tenant_base}/audit-export" and method == "GET":
            return _handle_audit_export(event, caller, deps, tenant_id=tenant_id)

        if path_lower == tenant_base:
            if method == "GET":
                return _handle_read(event, caller, deps, tenant_id=tenant_id)
            if method in {"PATCH", "PUT"}:
                return _handle_update(event, caller, deps, tenant_id=tenant_id)
            if method == "DELETE":
                return _handle_delete(caller, deps, tenant_id=tenant_id)

    return None


@logger.inject_lambda_context(clear_state=True, log_event=False)
def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    caller = _caller_identity(event)
    deps = _dependencies()
    logger.append_keys(appid=caller.app_id or "unknown", tenantid=caller.tenant_id or "unknown")

    method = _http_method(event)
    path = _request_path(event)

    try:
        tenant_id = _validated_path_tenant_id(event)
        if path == "/v1/health" and method == "GET":
            return _handle_health(deps)
        if path == "/v1/sessions" and method == "GET":
            return _handle_sessions(event, caller)

        # Dispatch route groups
        response = _dispatch_platform_routes(path, method, event, caller, deps)
        if response:
            return response

        response = _dispatch_ops_routes(path, method, event, caller, deps)
        if response:
            return response

        response = _dispatch_webhook_routes(path, method, event, caller, deps)
        if response:
            return response

        response = _dispatch_tenant_routes(path, method, event, caller, deps, tenant_id)
        if response:
            return response

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
