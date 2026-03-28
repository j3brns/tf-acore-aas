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
from data_access import ControlPlaneDynamoDB, TenantContext, TenantScopedDynamoDB, TenantScopedS3
from data_access.models import (
    AGENT_STATUS_TRANSITIONS,
    REGISTERABLE_AGENT_STATUSES,
    AgentStatus,
    TenantStatus,
    TenantTier,
    normalize_agent_status,
)

logger = Logger(service="tenant-api")

_TENANTS_TABLE_ENV = "TENANTS_TABLE_NAME"
_AGENTS_TABLE_ENV = "AGENTS_TABLE_NAME"
_INVOCATIONS_TABLE_ENV = "INVOCATIONS_TABLE_NAME"
_EVENT_BUS_ENV = "EVENT_BUS_NAME"
_AUDIT_EXPORT_BUCKET_ENV = "AUDIT_EXPORT_BUCKET"
_API_KEY_SECRET_PREFIX_ENV = "TENANT_API_KEY_SECRET_PREFIX"  # pragma: allowlist secret
_TENANT_MGMT_ROLE_ARN_ENV = "TENANT_MGMT_ROLE_ARN"
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
_RESERVED_TENANT_IDS = frozenset({"platform", "admin", "root", "system", "stub"})
_DEFAULT_OPS_LOCKS_TABLE = "platform-ops-locks"
_DEFAULT_RUNTIME_REGION_PARAM = "/platform/config/runtime-region"
_DEFAULT_FALLBACK_REGION_PARAM = "/platform/config/fallback-region"
_DEFAULT_FAILOVER_LOCK_NAME = "platform-runtime-failover"
_AGENTCORE_QUOTA_NAME = "Active session workloads per account"
_AGENTCORE_CONCURRENT_SESSIONS_NAMESPACE = "AgentCore"
_AGENTCORE_CONCURRENT_SESSIONS_METRIC = "ConcurrentSessions"
_AGENTCORE_QUOTA_LOOKBACK_MINUTES = 5
_TENANT_PROVISIONING_STATUSES = frozenset({"pending", "provisioning", "ready", "failed"})


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


def _validated_path_tenant_id(event: dict[str, Any], *, allow_reserved: bool = False) -> str | None:
    tenant_id = _path_tenant_id(event)
    if tenant_id is None:
        return None
    # Path-based tenant routes use the same canonicalization and validation
    # contract as tenant creation so auth decisions never depend on raw casing.
    return _canonical_tenant_id(tenant_id, allow_reserved=allow_reserved)


def _tenant_pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def _tenant_key(tenant_id: str) -> dict[str, str]:
    return {"PK": _tenant_pk(tenant_id), "SK": "METADATA"}


def _canonical_tenant_id(value: Any, *, allow_reserved: bool = False) -> str:
    tenant_id = _str_or_none(value)
    if tenant_id is None:
        raise ValueError("tenantId is required")

    normalized = tenant_id.lower()
    if len(normalized) < _TENANT_ID_MIN_LENGTH or len(normalized) > _TENANT_ID_MAX_LENGTH:
        raise ValueError("tenantId must be 3-32 characters")
    if "--" in normalized:
        raise ValueError("tenantId must not contain consecutive hyphens")
    if normalized in _RESERVED_TENANT_IDS and not (allow_reserved and normalized == "platform"):
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


def _agents_table_name() -> str:
    return os.environ.get(_AGENTS_TABLE_ENV, "platform-agents")


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


def _control_plane_db(caller: CallerIdentity) -> ControlPlaneDynamoDB:
    tenant_context = _tenant_context_for_scope(
        tenant_id=caller.tenant_id or "control-plane",
        caller=caller,
        app_id=caller.app_id or "control-plane",
    )
    return ControlPlaneDynamoDB(tenant_context)


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


def _normalize_agent_status(value: Any) -> AgentStatus:
    try:
        return normalize_agent_status(_str_or_none(value))
    except ValueError as exc:
        allowed = ", ".join(status.value for status in AgentStatus)
        raise ValueError(f"status must be one of: {allowed}") from exc


def _agent_event_detail_type(status: AgentStatus) -> str | None:
    if status is AgentStatus.PROMOTED:
        return "platform.agent_version.promoted"
    if status is AgentStatus.ROLLED_BACK:
        return "platform.agent_version.rolled_back"
    return None


def _agent_release_operation(status: AgentStatus) -> str | None:
    if status is AgentStatus.PROMOTED:
        return "promotion"
    if status is AgentStatus.ROLLED_BACK:
        return "rollback"
    return None


def _build_agent_release_lifecycle_event_detail(
    *,
    caller: CallerIdentity,
    agent_name: str,
    version: str,
    previous_status: AgentStatus,
    new_status: AgentStatus,
    occurred_at: str,
    approved_by: str | None,
    approved_at: str | None,
    release_notes: str | None,
    evaluation_score: float | None,
    evaluation_report_url: str | None,
    rolled_back_by: str | None,
    rolled_back_at: str | None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": _agent_release_operation(new_status),
        "occurredAt": occurred_at,
        "actorTenantId": caller.tenant_id or "platform",
        "actorAppId": caller.app_id,
        "actorSub": caller.sub,
        "releaseId": f"{agent_name}:{version}",
        "agentRecordPk": f"AGENT#{agent_name}",
        "agentRecordSk": f"VERSION#{version}",
        "agentName": agent_name,
        "version": version,
        "previousStatus": previous_status.value,
        "status": new_status.value,
        "approvedBy": approved_by,
        "approvedAt": approved_at,
        "releaseNotes": release_notes,
        "evaluationScore": evaluation_score,
        "evaluationReportUrl": evaluation_report_url,
        "rolledBackBy": rolled_back_by,
        "rolledBackAt": rolled_back_at,
    }


def _validate_agent_status_transition(
    current_status: AgentStatus,
    new_status: AgentStatus,
) -> None:
    if new_status == current_status:
        return
    allowed = AGENT_STATUS_TRANSITIONS.get(current_status, frozenset())
    if new_status not in allowed:
        allowed_text = ", ".join(status.value for status in sorted(allowed, key=lambda s: s.value))
        raise ValueError(
            f"Invalid agent status transition: {current_status.value} -> {new_status.value}. "
            f"Allowed transitions: {allowed_text or 'none'}"
        )


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


def _read_failover_lock_record(
    caller: CallerIdentity, deps: TenantApiDependencies
) -> dict[str, Any] | None:
    _ = deps
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
    _attach_tenant_api_key_secret_policy(
        deps,
        secret_arn=str(response["ARN"]),
        tenant_id=tenant_id,
        app_id=app_id,
    )
    return str(response["ARN"])


def _attach_tenant_api_key_secret_policy(
    deps: TenantApiDependencies,
    *,
    secret_arn: str,
    tenant_id: str,
    app_id: str,
) -> None:
    tenant_mgmt_role_arn = os.environ.get(_TENANT_MGMT_ROLE_ARN_ENV, "").strip()
    if not tenant_mgmt_role_arn:
        logger.warning(
            "Skipping tenant API key secret resource policy: manager role ARN not configured",
            extra={"tenant_id": tenant_id, "app_id": app_id},
        )
        return

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyTenantMgmtReadback",
                "Effect": "Deny",
                "Principal": {"AWS": tenant_mgmt_role_arn},
                "Action": "secretsmanager:GetSecretValue",
                "Resource": secret_arn,
            }
        ],
    }
    deps.secretsmanager.put_resource_policy(
        SecretId=secret_arn,
        ResourcePolicy=json.dumps(policy, separators=(",", ":")),
        BlockPublicPolicy=True,
    )


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
        "provisioningStatus",
        "provisioningUpdatedAt",
        "provisioningError",
        "apiKeySecretArn",
        "monthlyBudgetUsd",
        "deletedAt",
        "purgeAtEpochSeconds",
    )
    for field in optional_fields:
        if field in item and item[field] is not None:
            record[field] = item[field]
    return record


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
    if path in {
        "/v1/platform/failover",
        "/v1/platform/quota",
        "/v1/platform/quota/split-accounts",
        "/v1/platform/service-health",
        "/v1/platform/billing/status",
    }:
        try:
            import ops_control
        except ImportError:  # pragma: no cover - local package import path
            from src.tenant_api import ops_control
        return ops_control.dispatch_platform_admin_routes(path, method, event, caller, deps)

    if path.startswith("/v1/platform/agents"):
        try:
            import agent_registry
        except ImportError:  # pragma: no cover - local package import path
            from src.tenant_api import agent_registry
        return agent_registry.dispatch_routes(path, method, event, caller, deps)

    return None


def _dispatch_ops_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any] | None:
    try:
        import ops_control
    except ImportError:  # pragma: no cover - local package import path
        from src.tenant_api import ops_control
    return ops_control.dispatch_ops_routes(path, method, event, caller, deps)


def _dispatch_webhook_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
) -> dict[str, Any] | None:
    try:
        import webhook_registry
    except ImportError:  # pragma: no cover - local package import path
        from src.tenant_api import webhook_registry
    return webhook_registry.dispatch_routes(path, method, event, caller, deps)


def _dispatch_tenant_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: CallerIdentity,
    deps: TenantApiDependencies,
    tenant_id: str | None,
) -> dict[str, Any] | None:
    try:
        import tenant_lifecycle
    except ImportError:  # pragma: no cover - local package import path
        from src.tenant_api import tenant_lifecycle
    return tenant_lifecycle.dispatch_routes(path, method, event, caller, deps, tenant_id)


@logger.inject_lambda_context(clear_state=True, log_event=False)
def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    deps = _dependencies()
    detail_type = _str_or_none(event.get("detail-type"))
    source = _str_or_none(event.get("source"))
    if detail_type and source == "platform.tenant_provisioner":
        detail = event.get("detail") or {}
        tenant_id = _str_or_none(detail.get("tenantId")) if isinstance(detail, dict) else None
        app_id = _str_or_none(detail.get("appId")) if isinstance(detail, dict) else None
        logger.append_keys(appid=app_id or "unknown", tenantid=tenant_id or "unknown")
        try:
            try:
                import tenant_lifecycle
            except ImportError:  # pragma: no cover - local package import path
                from src.tenant_api import tenant_lifecycle
            return tenant_lifecycle.handle_tenant_provisioning_event(event, deps)
        except ValueError as exc:
            return _error(400, "BAD_REQUEST", str(exc))
        except ClientError as exc:
            logger.exception("AWS client error in tenant provisioning event handler")
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            return _error(502, "AWS_CLIENT_ERROR", error_code)

    caller = _caller_identity(event)
    logger.append_keys(appid=caller.app_id or "unknown", tenantid=caller.tenant_id or "unknown")

    method = _http_method(event)
    path = _request_path(event)

    try:
        tenant_id = _validated_path_tenant_id(event, allow_reserved=caller.is_admin)
        if path == "/v1/health" and method == "GET":
            try:
                import tenant_lifecycle
            except ImportError:  # pragma: no cover - local package import path
                from src.tenant_api import tenant_lifecycle
            return tenant_lifecycle.handle_health(deps)
        if path == "/v1/sessions" and method == "GET":
            try:
                import tenant_lifecycle
            except ImportError:  # pragma: no cover - local package import path
                from src.tenant_api import tenant_lifecycle
            return tenant_lifecycle.handle_sessions(event, caller)

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
