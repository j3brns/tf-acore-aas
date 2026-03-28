from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

try:
    import handler as shared
except ImportError:  # pragma: no cover - local package import path
    from src.tenant_api import handler as shared


PLATFORM_ADMIN_PATHS = {
    "/v1/platform/failover",
    "/v1/platform/quota",
    "/v1/platform/quota/split-accounts",
    "/v1/platform/service-health",
    "/v1/platform/billing/status",
}


def handle_platform_failover(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    shared._require_admin(caller)
    body = shared._require_json_body(event)
    target_region = shared._str_or_none(body.get("targetRegion"))
    lock_id = shared._str_or_none(body.get("lockId"))

    if not target_region or not lock_id:
        raise ValueError("targetRegion and lockId are required")

    lock_record = shared._read_failover_lock_record(caller, deps)
    if lock_record is None:
        shared.logger.warning(
            "Platform failover rejected: lock missing",
            extra={
                "actor": caller.sub,
                "lock_id": lock_id,
                "target_region": target_region,
                "lock_name": shared._failover_lock_name(),
            },
        )
        return shared._error(409, "LOCK_NOT_HELD", "Runtime failover lock is not currently held")

    current_lock_id = shared._str_or_none(lock_record.get("lockId") or lock_record.get("lock_id"))
    acquired_by = shared._str_or_none(
        lock_record.get("acquiredBy") or lock_record.get("acquired_by")
    )
    ttl_raw = lock_record.get("ttl")
    if ttl_raw is None:
        raise ValueError("Failover lock record is invalid")
    try:
        ttl = int(ttl_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Failover lock record is invalid") from exc

    now_epoch = int(shared._now_utc().timestamp())
    if ttl <= now_epoch:
        shared.logger.warning(
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
        return shared._error(409, "LOCK_EXPIRED", "Runtime failover lock has expired")

    if current_lock_id != lock_id:
        shared.logger.warning(
            "Platform failover rejected: lock mismatch",
            extra={
                "actor": caller.sub,
                "lock_id": lock_id,
                "current_lock_id": current_lock_id,
                "target_region": target_region,
                "lock_owner": acquired_by,
            },
        )
        return shared._error(
            409,
            "LOCK_MISMATCH",
            "Runtime failover lock is held by another actor or session",
        )

    current_region = str(
        deps.ssm.get_parameter(Name=shared._runtime_region_param_name())["Parameter"]["Value"]
    ).strip()
    if current_region == target_region:
        shared.logger.info(
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
        return shared._response(
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
            Name=shared._runtime_region_param_name(),
            Value=target_region,
            Type="String",
            Overwrite=True,
        )
    except ClientError:
        shared.logger.exception(
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

    shared.logger.info(
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

    return shared._response(
        200,
        {
            "status": "completed",
            "region": target_region,
            "previousRegion": current_region,
            "lockId": lock_id,
            "changed": True,
        },
    )


def handle_platform_quota(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    shared._require_admin(caller)
    active_region = shared._required_ssm_parameter(deps.ssm, shared._runtime_region_param_name())
    fallback_region = shared._optional_ssm_parameter(deps.ssm, shared._fallback_region_param_name())
    utilisation = deps.platform_quota_client.get_utilisation(
        active_region=active_region,
        fallback_region=fallback_region,
    )
    return shared._response(200, {"utilisation": utilisation})


def handle_platform_split_accounts(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    if "Platform.Admin" not in caller.roles:
        raise PermissionError("Platform.Admin role required")

    body = shared._require_json_body(event)
    tier = shared._normalize_tier(body.get("tier"))
    target_account_id = shared._require_aws_account_id(
        body.get("targetAccountId"),
        field="targetAccountId",
    )

    job_id = f"job-split-{secrets.token_hex(4)}"
    shared.logger.info(
        "Account split initiated",
        extra={"tier": tier, "target_account_id": target_account_id, "job_id": job_id},
    )

    return shared._response(202, {"status": "initiated", "jobId": job_id})


def handle_platform_service_health(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    shared._require_admin(caller)
    return shared._response(
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


def handle_platform_billing_status(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    shared._require_admin(caller)
    year_month = datetime.now(UTC).strftime("%Y-%m")
    db = shared._control_plane_db(caller)
    summaries = db.scan_all(
        shared._tenants_table_name(),
        filter_expression=Key("SK").eq(f"BILLING#{year_month}"),
    )

    total_cost = sum(float(s.get("total_cost_usd", 0.0)) for s in summaries)
    total_input = sum(int(s.get("total_input_tokens", 0)) for s in summaries)
    total_output = sum(int(s.get("total_output_tokens", 0)) for s in summaries)

    return shared._response(
        200,
        {
            "status": "active",
            "yearMonth": year_month,
            "tenantCount": len(summaries),
            "totalCostUsd": round(total_cost, 2),
            "totalTokens": total_input + total_output,
            "lastUpdated": shared._iso(shared._now_utc()),
        },
    )


def handle_ops_top_tenants(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = caller
    _ = deps
    query = event.get("queryStringParameters") or {}
    n = int(query.get("n", 10))
    return shared._response(
        200,
        {
            "tenants": [
                {"tenantId": f"t-{i:03d}", "tokens": 1000000 - (i * 10000)} for i in range(1, n + 1)
            ]
        },
    )


def handle_ops_security_events(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = event
    _ = caller
    _ = deps
    return shared._response(
        200,
        {
            "events": [
                {
                    "timestamp": shared._iso(shared._now_utc() - timedelta(minutes=5)),
                    "type": "tenant_access_violation",
                    "tenantId": "t-suspicious",
                    "details": "Cross-tenant partition access attempt detected",
                }
            ]
        },
    )


def handle_ops_error_rate(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = caller
    _ = deps
    return shared._response(
        200,
        {
            "errorRate": 0.02,
            "periodMinutes": int((event.get("queryStringParameters") or {}).get("minutes", 5)),
            "threshold": 0.05,
        },
    )


def handle_ops_dlq_inspect(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    queue_name: str,
) -> dict[str, Any]:
    _ = caller
    _ = deps
    return shared._response(
        200,
        {
            "queueName": queue_name,
            "approximateNumberOfMessages": 3,
            "messages": [
                {
                    "messageId": f"msg-{i}",
                    "timestamp": shared._iso(shared._now_utc()),
                    "body": {"jobId": f"job-{i}"},
                }
                for i in range(3)
            ],
        },
    )


def handle_ops_dlq_redrive(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    queue_name: str,
) -> dict[str, Any]:
    _ = caller
    _ = deps
    _ = queue_name
    return shared._response(200, {"status": "initiated", "redriveCount": 3})


def handle_ops_tenant_sessions(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    _ = caller
    _ = deps
    return shared._response(
        200,
        {
            "tenantId": tenant_id,
            "activeSessions": [
                {"sessionId": f"sess-{i}", "lastActivity": shared._iso(shared._now_utc())}
                for i in range(2)
            ],
        },
    )


def handle_ops_suspend_tenant(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    _ = caller
    _ = deps
    body = shared._require_json_body(event)
    reason = body.get("reason", "No reason provided")
    return shared._response(200, {"tenantId": tenant_id, "status": "suspended", "reason": reason})


def handle_ops_reinstate_tenant(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    _ = caller
    _ = deps
    return shared._response(200, {"tenantId": tenant_id, "status": "active"})


def handle_ops_invocation_report(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    _ = event
    _ = caller
    _ = deps
    return shared._response(
        200,
        {
            "tenantId": tenant_id,
            "totalInvocations": 1250,
            "successRate": 0.992,
            "avgLatencyMs": 450,
        },
    )


def handle_ops_notify_tenant(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    _ = caller
    _ = deps
    body = shared._require_json_body(event)
    template = body.get("template")
    return shared._response(200, {"status": "sent", "tenantId": tenant_id, "template": template})


def handle_ops_fail_job(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    job_id: str,
) -> dict[str, Any]:
    _ = caller
    _ = deps
    body = shared._require_json_body(event)
    reason = body.get("reason")
    return shared._response(200, {"jobId": job_id, "status": "failed", "reason": reason})


def handle_ops_lambda_rollback(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    shared._require_admin(caller)
    body = shared._require_json_body(event)
    suffix = shared._str_or_none(body.get("functionSuffix"))
    alias_name = shared._str_or_none(body.get("aliasName")) or "live"

    if suffix is None:
        raise ValueError("functionSuffix is required")

    current_fn = shared.os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "platform-tenant-api-dev")
    env = current_fn.split("-")[-1]
    full_name = f"platform-{suffix}-{env}"

    shared.logger.info(
        "Initiating Lambda rollback",
        extra={"target_function": full_name, "alias": alias_name, "actor": caller.sub},
    )

    try:
        alias = deps.awslambda.get_alias(FunctionName=full_name, Name=alias_name)
        current_version = alias["FunctionVersion"]
        versions: list[str] = []
        paginator = deps.awslambda.get_paginator("list_versions_by_function")
        for page in paginator.paginate(FunctionName=full_name):
            for version in page.get("Versions", []):
                version_number = version["Version"]
                if version_number != "$LATEST":
                    versions.append(version_number)

        versions.sort(key=lambda item: int(item))

        if current_version not in versions:
            return shared._error(
                409,
                "CONFLICT",
                f"Current version {current_version} not found in published versions list",
            )

        idx = versions.index(current_version)
        if idx == 0:
            return shared._error(
                409,
                "NO_PREVIOUS_VERSION",
                f"Version {current_version} is the oldest published version; cannot roll back.",
            )

        previous_version = versions[idx - 1]
        deps.awslambda.update_alias(
            FunctionName=full_name,
            Name=alias_name,
            FunctionVersion=previous_version,
            Description=f"Rollback from {current_version} to {previous_version} by {caller.sub}",
        )

        shared.logger.info(
            "Lambda rollback completed",
            extra={
                "target_function": full_name,
                "alias": alias_name,
                "from_version": current_version,
                "to_version": previous_version,
            },
        )

        return shared._response(
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
            return shared._error(
                404,
                "NOT_FOUND",
                f"Function or alias not found: {full_name}:{alias_name}",
            )
        shared.logger.exception("Lambda rollback failed")
        return shared._error(500, "INTERNAL_ERROR", f"AWS Error: {code}")


def handle_ops_page_security(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = caller
    _ = deps
    shared._require_json_body(event)
    return shared._response(200, {"status": "paged", "incidentId": f"inc-{secrets.token_hex(4)}"})


def dispatch_platform_admin_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any] | None:
    if path == "/v1/platform/failover" and method == "POST":
        return handle_platform_failover(event, caller, deps)
    if path == "/v1/platform/quota" and method == "GET":
        return handle_platform_quota(caller, deps)
    if path == "/v1/platform/quota/split-accounts" and method == "POST":
        return handle_platform_split_accounts(event, caller, deps)
    if path == "/v1/platform/service-health" and method == "GET":
        return handle_platform_service_health(caller, deps)
    if path == "/v1/platform/billing/status" and method == "GET":
        return handle_platform_billing_status(caller, deps)
    return None


def dispatch_ops_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any] | None:
    path_lower = path.lower()
    if not path_lower.startswith("/v1/platform/ops/"):
        return None

    shared._require_admin(caller)
    if path_lower == "/v1/platform/ops/top-tenants" and method == "GET":
        return handle_ops_top_tenants(event, caller, deps)
    if path_lower == "/v1/platform/ops/security-events" and method == "GET":
        return handle_ops_security_events(event, caller, deps)
    if path_lower == "/v1/platform/ops/error-rate" and method == "GET":
        return handle_ops_error_rate(event, caller, deps)
    if path_lower == "/v1/platform/ops/lambda-rollback" and method == "POST":
        return handle_ops_lambda_rollback(event, caller, deps)

    parts = path.split("/")
    if path_lower.startswith("/v1/platform/ops/dlq/"):
        if len(parts) == 6 and method == "GET":
            return handle_ops_dlq_inspect(caller, deps, queue_name=parts[5])
        if len(parts) == 7 and parts[6].lower() == "redrive" and method == "POST":
            return handle_ops_dlq_redrive(caller, deps, queue_name=parts[5])

    if path_lower.startswith("/v1/platform/ops/tenants/") and len(parts) == 7:
        tenant_id = parts[5]
        subpath = parts[6].lower()
        if subpath == "sessions" and method == "GET":
            return handle_ops_tenant_sessions(caller, deps, tenant_id=tenant_id)
        if subpath == "suspend" and method == "POST":
            return handle_ops_suspend_tenant(event, caller, deps, tenant_id=tenant_id)
        if subpath == "reinstate" and method == "POST":
            return handle_ops_reinstate_tenant(caller, deps, tenant_id=tenant_id)
        if subpath == "invocations" and method == "GET":
            return handle_ops_invocation_report(event, caller, deps, tenant_id=tenant_id)
        if subpath == "notify" and method == "POST":
            return handle_ops_notify_tenant(event, caller, deps, tenant_id=tenant_id)

    if path_lower.startswith("/v1/platform/ops/jobs/"):
        if len(parts) == 7 and parts[6].lower() == "fail" and method == "POST":
            return handle_ops_fail_job(event, caller, deps, job_id=parts[5])

    if path_lower == "/v1/platform/ops/security/page" and method == "POST":
        return handle_ops_page_security(event, caller, deps)

    return None
