from __future__ import annotations

from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

try:
    import handler as shared

    from . import auth, db_factory, db_utils, http_utils, models, utils
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import (
        auth,
        db_factory,
        db_utils,
        http_utils,
        models,
        utils,
    )
    from src.tenant_api import (
        handler as shared,
    )


PLATFORM_ADMIN_PATHS = {
    "/v1/platform/failover",
    "/v1/platform/quota",
    "/v1/platform/quota/split-accounts",
    "/v1/platform/service-health",
    "/v1/platform/billing/status",
}


def handle_platform_failover(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    auth.require_admin(caller)
    auth.require_platform_actor(caller)
    body = http_utils.require_json_body(event)
    target_region = utils.str_or_none(body.get("targetRegion"))
    lock_id = utils.str_or_none(body.get("lockId"))

    if not target_region or not lock_id:
        raise ValueError("targetRegion and lockId are required")

    lock_record = db_utils.read_failover_lock_record(caller, deps)
    if lock_record is None:
        shared.logger.warning(
            "Platform failover rejected: lock missing",
            extra={
                "actor": caller.sub,
                "lock_id": lock_id,
                "target_region": target_region,
                "lock_name": db_factory.ops_locks_table_name(),
            },
        )
        return http_utils.error(409, "LOCK_NOT_HELD", "Runtime failover lock is not currently held")

    current_lock_id = utils.str_or_none(lock_record.get("lockId") or lock_record.get("lock_id"))
    acquired_by = utils.str_or_none(lock_record.get("acquiredBy") or lock_record.get("acquired_by"))

    if current_lock_id != lock_id or acquired_by != caller.sub:
        shared.logger.warning(
            "Platform failover rejected: lock mismatch",
            extra={
                "actor": caller.sub,
                "lock_id": lock_id,
                "current_lock_id": current_lock_id,
                "acquired_by": acquired_by,
            },
        )
        return http_utils.error(409, "LOCK_MISMATCH", "Failover lock is held by another session")

    try:
        ssm = deps.ssm
        # ADR-009: Runtime region zigzag (London <-> Dublin)
        ssm.put_parameter(
            Name=db_factory.runtime_region_param_name(),
            Value=target_region,
            Type="String",
            Overwrite=True,
        )
        shared.logger.warning(
            "Platform regional failover executed",
            extra={
                "actor": caller.sub,
                "target_region": target_region,
                "lock_id": lock_id,
            },
        )
        return http_utils.response(
            200, {"activeRegion": target_region, "status": "failover_executed"}
        )
    except ClientError as exc:
        shared.logger.exception("Failed to update runtime region in SSM")
        return http_utils.error(502, "AWS_SSM_ERROR", str(exc))


def handle_platform_quota(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    _ = event
    auth.require_admin(caller)
    auth.require_platform_actor(caller)

    ssm = deps.ssm
    active_region = shared._required_ssm_parameter(ssm, db_factory.runtime_region_param_name())
    fallback_region = shared._optional_ssm_parameter(ssm, db_factory.fallback_region_param_name())

    # Get real-time utilization from CloudWatch/Service Quotas
    quotas = deps.platform_quota_client.get_utilisation(
        active_region=active_region,
        fallback_region=fallback_region,
    )

    return http_utils.response(200, {"quotas": quotas})


def handle_platform_billing_status(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    auth.require_admin(caller)
    auth.require_platform_actor(caller)
    year_month = utils.now_utc().strftime("%Y-%m")
    db = db_factory.control_plane_db(caller)
    summaries = db.scan_all(
        db_factory.tenants_table_name(),
        filter_expression=Key("SK").eq(f"BILLING#{year_month}"),
    )

    return http_utils.response(
        200,
        {
            "yearMonth": year_month,
            "summaries": [
                {
                    "tenantId": s.get("tenantId") or s.get("tenant_id"),
                    "totalInputTokens": int(
                        s.get("totalInputTokens", s.get("total_input_tokens", 0))
                    ),
                    "totalOutputTokens": int(
                        s.get("totalOutputTokens", s.get("total_output_tokens", 0))
                    ),
                    "totalCostUsd": float(s.get("totalCostUsd", s.get("total_cost_usd", 0.0))),
                    "lastUpdated": s.get("updatedAt") or s.get("last_updated"),
                }
                for s in summaries
            ],
        },
    )


def handle_service_health(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    _ = event
    _ = deps
    auth.require_admin(caller)
    # Aggregate health of platform components
    return http_utils.response(
        200,
        {
            "status": "operational",
            "services": {
                "tenant-api": "operational",
                "bridge": "operational",
                "authoriser": "operational",
                "data-access-lib": "operational",
            },
            "timestamp": utils.iso(utils.now_utc()),
        },
    )


def dispatch_platform_admin_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any] | None:
    if path == "/v1/platform/failover" and method == "POST":
        return handle_platform_failover(event, caller, deps)
    if path == "/v1/platform/quota" and method == "GET":
        return handle_platform_quota(event, caller, deps)
    if path == "/v1/platform/billing/status" and method == "GET":
        return handle_platform_billing_status(event, caller, deps)
    if path == "/v1/platform/service-health" and method == "GET":
        return handle_service_health(event, caller, deps)
    return None


def dispatch_ops_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any] | None:
    # Operations routes (can be accessed by platform admin or tenant admin)
    _ = path
    _ = method
    _ = event
    _ = caller
    _ = deps
    return None
