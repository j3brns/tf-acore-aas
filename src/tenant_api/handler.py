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
from datetime import UTC, datetime, timedelta
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

from src.tenant_api import (
    agent_logic,
    auth,
    constants,
    db_factory,
    db_utils,
    events,
    http_utils,
    lifecycle_logic,
    models,
    secrets_manager,
    serialization,
    utils,
    validation,
)
from src.tenant_api.auth import (
    can_manage_tenant_self_service as _can_manage_tenant_self_service,
)
from src.tenant_api.auth import (
    can_read_tenant as _can_read_tenant,
)
from src.tenant_api.auth import (
    is_self_service_admin as _is_self_service_admin,
)
from src.tenant_api.auth import (
    require_admin as _require_admin,
)
from src.tenant_api.auth import (
    require_platform_actor as _require_platform_actor,
)
from src.tenant_api.constants import (
    ADMIN_ROLES,
    AGENTCORE_CONCURRENT_SESSIONS_METRIC,
    AGENTCORE_CONCURRENT_SESSIONS_NAMESPACE,
    AGENTCORE_QUOTA_LOOKBACK_MINUTES,
    AGENTCORE_QUOTA_NAME,
    AGENTS_TABLE_ENV,
    ALLOWED_TENANT_INVITE_ROLES,
    API_KEY_SECRET_PREFIX_ENV,
    AUDIT_EXPORT_BUCKET_ENV,
    AUDIT_EXPORT_PAGE_SIZE,
    AUDIT_EXPORT_PREFIX,
    AUDIT_EXPORT_URL_EXPIRY_SECONDS,
    AWS_ACCOUNT_ID_PATTERN,
    DEFAULT_FAILOVER_LOCK_NAME,
    DEFAULT_FALLBACK_REGION_PARAM,
    DEFAULT_OPS_LOCKS_TABLE,
    DEFAULT_RUNTIME_REGION_PARAM,
    DELETE_RETENTION_DAYS,
    EVENT_BUS_ENV,
    FAILOVER_LOCK_NAME_ENV,
    FALLBACK_REGION_PARAM_ENV,
    INVITE_EXPIRY_DAYS,
    INVOCATIONS_TABLE_ENV,
    OPS_LOCKS_TABLE_ENV,
    PLATFORM_TENANT_ID,
    RESERVED_TENANT_IDS,
    RUNTIME_REGION_PARAM_ENV,
    SELF_SERVICE_ADMIN_ROLES,
    TENANT_ID_MAX_LENGTH,
    TENANT_ID_MIN_LENGTH,
    TENANT_ID_PATTERN,
    TENANT_MGMT_ROLE_ARN_ENV,
    TENANT_PROVISIONING_STATUSES,
    TENANTS_TABLE_ENV,
)
from src.tenant_api.db_factory import (
    audit_export_bucket_name as _audit_export_bucket_name,
)
from src.tenant_api.db_factory import (
    control_plane_db as _control_plane_db,
)
from src.tenant_api.db_factory import (
    db_for_tenant as _db_for_tenant,
)
from src.tenant_api.db_factory import (
    invocations_table_name as _invocations_table_name,
)
from src.tenant_api.db_factory import (
    s3_for_tenant as _s3_for_tenant,
)
from src.tenant_api.db_factory import (
    tenants_table_name as _tenants_table_name,
)
from src.tenant_api.db_utils import (
    build_update_expression as _build_update_expression,
)
from src.tenant_api.db_utils import (
    ddb_value as _ddb_value,
)
from src.tenant_api.db_utils import (
    read_failover_lock_record as _read_failover_lock_record,
)
from src.tenant_api.db_utils import (
    read_tenant_record as _read_tenant_record,
)
from src.tenant_api.db_utils import (
    tenant_key as _tenant_key,
)
from src.tenant_api.db_utils import (
    tenant_pk as _tenant_pk,
)
from src.tenant_api.events import (
    event_bus_name as _event_bus_name,
)
from src.tenant_api.events import (
    put_event as _put_event,
)
from src.tenant_api.http_utils import (
    caller_identity as _caller_identity,
)
from src.tenant_api.http_utils import (
    error as _error,
)
from src.tenant_api.http_utils import (
    get_authorizer_map,
    parse_roles,
    require_json_body,
)
from src.tenant_api.http_utils import (
    response as _response,
)
from src.tenant_api.models import CallerIdentity, TenantApiDependencies
from src.tenant_api.secrets_manager import (
    attach_tenant_api_key_secret_policy as _attach_tenant_api_key_secret_policy,
)
from src.tenant_api.secrets_manager import (
    create_api_key_secret as _create_api_key_secret,
)
from src.tenant_api.secrets_manager import (
    secret_prefix as _secret_prefix,
)
from src.tenant_api.serialization import (
    serialize_tenant as _serialize_tenant,
)
from src.tenant_api.utils import (
    coerce_positive_int,
    iso,
    json_default,
    now_utc,
    str_or_none,
)
from src.tenant_api.validation import (
    canonical_tenant_id as _canonical_tenant_id,
)
from src.tenant_api.validation import (
    parse_optional_utc_timestamp,
    parse_utc_timestamp,
    require_aws_account_id,
)

logger = Logger(service="tenant-api")

# Backward-compatibility aliases for tests and submodules
_now_utc = now_utc
_iso = iso
_str_or_none = str_or_none
_parse_roles = parse_roles
_canonical_tenant_id = _canonical_tenant_id
_require_aws_account_id = require_aws_account_id
_parse_utc_timestamp = parse_utc_timestamp
_parse_optional_utc_timestamp = parse_optional_utc_timestamp
_coerce_positive_int = coerce_positive_int
_as_float = utils.as_float
_json_default = json_default
_PLATFORM_TENANT_ID = PLATFORM_TENANT_ID

_normalize_agent_status = agent_logic.normalize_agent_status_val
_validate_agent_status_transition = agent_logic.validate_agent_status_transition
_agent_event_detail_type = agent_logic.agent_event_detail_type
_build_agent_release_lifecycle_event_detail = agent_logic.build_agent_release_lifecycle_event_detail
_platform_audit_envelope = agent_logic._platform_audit_envelope

_normalize_tier = lifecycle_logic.normalize_tier
_normalize_status = lifecycle_logic.normalize_status
_normalize_tenant_invite_role = lifecycle_logic.normalize_tenant_invite_role
_platform_control_response = lifecycle_logic.platform_control_response

_audit_export_bucket = db_factory.audit_export_bucket_name
_tenant_s3_for_scope = db_factory.s3_for_tenant


def _audit_export_url_expiry_seconds() -> int:
    return constants.AUDIT_EXPORT_URL_EXPIRY_SECONDS


def _failover_lock_name() -> str:
    return os.environ.get(FAILOVER_LOCK_NAME_ENV, DEFAULT_FAILOVER_LOCK_NAME)


_agents_table_name = db_factory.agents_table_name

_TENANTS_TABLE_ENV = TENANTS_TABLE_ENV
_AGENTS_TABLE_ENV = AGENTS_TABLE_ENV
_INVOCATIONS_TABLE_ENV = INVOCATIONS_TABLE_ENV
_EVENT_BUS_ENV = EVENT_BUS_ENV
_AUDIT_EXPORT_BUCKET_ENV = AUDIT_EXPORT_BUCKET_ENV
_API_KEY_SECRET_PREFIX_ENV = API_KEY_SECRET_PREFIX_ENV
_TENANT_MGMT_ROLE_ARN_ENV = TENANT_MGMT_ROLE_ARN_ENV
_OPS_LOCKS_TABLE_ENV = OPS_LOCKS_TABLE_ENV
_RUNTIME_REGION_PARAM_ENV = RUNTIME_REGION_PARAM_ENV
_FALLBACK_REGION_PARAM_ENV = FALLBACK_REGION_PARAM_ENV
_FAILOVER_LOCK_NAME_ENV = FAILOVER_LOCK_NAME_ENV
_DELETE_RETENTION_DAYS = DELETE_RETENTION_DAYS
_ADMIN_ROLES = ADMIN_ROLES
_SELF_SERVICE_ADMIN_ROLES = SELF_SERVICE_ADMIN_ROLES
_ALLOWED_TENANT_INVITE_ROLES = ALLOWED_TENANT_INVITE_ROLES
_INVITE_EXPIRY_DAYS = INVITE_EXPIRY_DAYS
_AUDIT_EXPORT_PREFIX = AUDIT_EXPORT_PREFIX
_AUDIT_EXPORT_URL_EXPIRY_SECONDS = AUDIT_EXPORT_URL_EXPIRY_SECONDS
_AUDIT_EXPORT_PAGE_SIZE = AUDIT_EXPORT_PAGE_SIZE
_TENANT_ID_MIN_LENGTH = TENANT_ID_MIN_LENGTH
_TENANT_ID_MAX_LENGTH = TENANT_ID_MAX_LENGTH
_TENANT_ID_PATTERN = TENANT_ID_PATTERN
_AWS_ACCOUNT_ID_PATTERN = AWS_ACCOUNT_ID_PATTERN
_RESERVED_TENANT_IDS = RESERVED_TENANT_IDS
_DEFAULT_OPS_LOCKS_TABLE = DEFAULT_OPS_LOCKS_TABLE
_DEFAULT_RUNTIME_REGION_PARAM = DEFAULT_RUNTIME_REGION_PARAM
_DEFAULT_FALLBACK_REGION_PARAM = DEFAULT_FALLBACK_REGION_PARAM
_DEFAULT_FAILOVER_LOCK_NAME = DEFAULT_FAILOVER_LOCK_NAME
_AGENTCORE_QUOTA_NAME = AGENTCORE_QUOTA_NAME
_AGENTCORE_CONCURRENT_SESSIONS_NAMESPACE = AGENTCORE_CONCURRENT_SESSIONS_NAMESPACE
_AGENTCORE_CONCURRENT_SESSIONS_METRIC = AGENTCORE_CONCURRENT_SESSIONS_METRIC
_AGENTCORE_QUOTA_LOOKBACK_MINUTES = AGENTCORE_QUOTA_LOOKBACK_MINUTES
_TENANT_PROVISIONING_STATUSES = TENANT_PROVISIONING_STATUSES


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
    return str_or_none(path_params.get("tenantId") or path_params.get("id"))


def _validated_path_tenant_id(event: dict[str, Any], *, allow_reserved: bool = False) -> str | None:
    tenant_id = _path_tenant_id(event)
    if tenant_id is None:
        return None
    # Path-based tenant routes use the same canonicalization and validation
    # contract as tenant creation so auth decisions never depend on raw casing.
    return _canonical_tenant_id(tenant_id, allow_reserved=allow_reserved)


def _format_export_timestamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ssm_parameter_value(ssm: Any, name: str, *, required: bool) -> str | None:
    try:
        response = ssm.get_parameter(Name=name)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if not required and error_code == "ParameterNotFound":
            return None
        raise

    value = str_or_none(response.get("Parameter", {}).get("Value"))
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


def _runtime_region_param_name() -> str:
    return os.environ.get(RUNTIME_REGION_PARAM_ENV, DEFAULT_RUNTIME_REGION_PARAM)


def _fallback_region_param_name() -> str:
    return os.environ.get(FALLBACK_REGION_PARAM_ENV, DEFAULT_FALLBACK_REGION_PARAM)


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
