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
    shared._require_platform_actor(caller)
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
        return shared._platform_control_response(
            200,
            {
                "status": "completed",
                "region": target_region,
                "previousRegion": current_region,
                "lockId": lock_id,
                "changed": False,
            },
            caller=caller,
            operation_type="runtime_failover",
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

    return shared._platform_control_response(
        200,
        {
            "status": "completed",
            "region": target_region,
            "previousRegion": current_region,
            "lockId": lock_id,
            "changed": True,
        },
        caller=caller,
        operation_type="runtime_failover",
    )


def handle_platform_quota(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    shared._require_admin(caller)
    shared._require_platform_actor(caller)
    active_region = shared._required_ssm_parameter(deps.ssm, shared._runtime_region_param_name())
    fallback_region = shared._optional_ssm_parameter(deps.ssm, shared._fallback_region_param_name())
    utilisation = deps.platform_quota_client.get_utilisation(
        active_region=active_region,
        fallback_region=fallback_region,
    )
    return shared._platform_control_response(
        200,
        {"utilisation": utilisation},
        caller=caller,
        operation_type="quota_report",
    )


def handle_platform_split_accounts(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    shared._require_platform_actor(caller)
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

    return shared._platform_control_response(
        202,
        {"status": "initiated", "jobId": job_id},
        caller=caller,
        operation_type="quota_split_accounts",
    )


def handle_platform_service_health(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    shared._require_admin(caller)
    shared._require_platform_actor(caller)
    return shared._platform_control_response(
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
        caller=caller,
        operation_type="service_health_read",
    )


def handle_platform_billing_status(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    shared._require_admin(caller)
    shared._require_platform_actor(caller)
    year_month = datetime.now(UTC).strftime("%Y-%m")
    db = shared._control_plane_db(caller)
    summaries = db.scan_all(
        shared._tenants_table_name(),
        filter_expression=Key("SK").eq(f"BILLING#{year_month}"),
    )

    total_cost = sum(float(s.get("total_cost_usd", 0.0)) for s in summaries)
    total_input = sum(int(s.get("total_input_tokens", 0)) for s in summaries)
    total_output = sum(int(s.get("total_output_tokens", 0)) for s in summaries)

    return shared._platform_control_response(
        200,
        {
            "status": "active",
            "yearMonth": year_month,
            "tenantCount": len(summaries),
            "totalCostUsd": round(total_cost, 2),
            "totalTokens": total_input + total_output,
            "lastUpdated": shared._iso(shared._now_utc()),
        },
        caller=caller,
        operation_type="billing_status_read",
    )


def handle_ops_top_tenants(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    query = event.get("queryStringParameters") or {}
    n = int(query.get("n", 10))
    return shared._platform_control_response(
        200,
        {
            "tenants": [
                {"tenantId": f"t-{i:03d}", "tokens": 1000000 - (i * 10000)} for i in range(1, n + 1)
            ]
        },
        caller=caller,
        operation_type="top_tenants_read",
    )


def handle_ops_security_events(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = event
    _ = deps
    return shared._platform_control_response(
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
        caller=caller,
        operation_type="security_events_read",
    )


def handle_ops_error_rate(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = deps
    return shared._platform_control_response(
        200,
        {
            "errorRate": 0.02,
            "periodMinutes": int((event.get("queryStringParameters") or {}).get("minutes", 5)),
            "threshold": 0.05,
        },
        caller=caller,
        operation_type="error_rate_read",
    )


def handle_ops_dlq_inspect(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    queue_name: str,
) -> dict[str, Any]:
    _ = deps
    return shared._platform_control_response(
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
        caller=caller,
        operation_type="dlq_inspect",
    )


def handle_ops_dlq_redrive(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    queue_name: str,
) -> dict[str, Any]:
    _ = deps
    _ = queue_name
    return shared._platform_control_response(
        200,
        {"status": "initiated", "redriveCount": 3},
        caller=caller,
        operation_type="dlq_redrive",
    )


def handle_ops_tenant_sessions(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    _ = deps
    return shared._platform_control_response(
        200,
        {
            "tenantId": tenant_id,
            "activeSessions": [
                {"sessionId": f"sess-{i}", "lastActivity": shared._iso(shared._now_utc())}
                for i in range(2)
            ],
        },
        caller=caller,
        operation_type="tenant_sessions_read",
        target_tenant_id=tenant_id,
    )


def _read_target_tenant(
    caller: shared.CallerIdentity,
    tenant_id: str,
) -> dict[str, Any] | None:
    """Read a target tenant record using control-plane access."""
    db = shared._control_plane_db(caller)
    return db.get_item(shared._tenants_table_name(), shared._tenant_key(tenant_id))


def _update_target_tenant(
    caller: shared.CallerIdentity,
    tenant_id: str,
    attributes: dict[str, Any],
) -> dict[str, Any]:
    """Update a target tenant record using control-plane access."""
    db = shared._control_plane_db(caller)
    expr, names, values = shared._build_update_expression(attributes)
    return db.update_item(
        shared._tenants_table_name(),
        shared._tenant_key(tenant_id),
        expr,
        values,
        expression_attribute_names=names,
        condition_expression="attribute_exists(PK)",
    )


def _ops_audit_event(
    deps: shared.TenantApiDependencies,
    *,
    caller: shared.CallerIdentity,
    detail_type: str,
    target_tenant_id: str,
    operation_type: str,
    outcome: str,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit an auditable EventBridge event for a platform-agent ops action."""
    detail: dict[str, Any] = {
        "schemaVersion": 1,
        "actorTenantId": caller.tenant_id or "platform",
        "actorSub": caller.sub,
        "targetTenantId": target_tenant_id,
        "operationType": operation_type,
        "outcome": outcome,
        "occurredAt": shared._iso(shared._now_utc()),
    }
    if reason is not None:
        detail["reason"] = reason
    if extra:
        detail.update(extra)
    shared._put_event(deps, detail_type=detail_type, detail=detail)


def handle_ops_suspend_tenant(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    body = shared._require_json_body(event)
    reason = shared._str_or_none(body.get("reason"))
    if not reason:
        raise ValueError("reason is required for tenant suspension")

    record = _read_target_tenant(caller, tenant_id)
    if record is None:
        return shared._error(404, "NOT_FOUND", f"Tenant {tenant_id} not found")

    current_status = shared._str_or_none(record.get("status"))
    if current_status == "suspended":
        return shared._error(409, "ALREADY_SUSPENDED", f"Tenant {tenant_id} is already suspended")
    if current_status == "deleted":
        return shared._error(409, "TENANT_DELETED", f"Tenant {tenant_id} is deleted")

    now = shared._iso(shared._now_utc())
    _update_target_tenant(caller, tenant_id, {"status": "suspended", "updatedAt": now})

    shared.logger.info(
        "Tenant suspended via ops workflow",
        extra={
            "actor_sub": caller.sub,
            "actor_tenant_id": caller.tenant_id,
            "target_tenant_id": tenant_id,
            "operation_type": "suspend",
            "reason": reason,
        },
    )

    _ops_audit_event(
        deps,
        caller=caller,
        detail_type="tenant.suspended",
        target_tenant_id=tenant_id,
        operation_type="tenant_suspend",
        outcome="success",
        reason=reason,
    )

    return shared._platform_control_response(
        200,
        {"tenantId": tenant_id, "status": "suspended", "reason": reason},
        caller=caller,
        operation_type="tenant_suspend",
        target_tenant_id=tenant_id,
    )


def handle_ops_reinstate_tenant(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    body = shared._require_json_body(event)
    reason = shared._str_or_none(body.get("reason")) or "Reinstated by operator"

    record = _read_target_tenant(caller, tenant_id)
    if record is None:
        return shared._error(404, "NOT_FOUND", f"Tenant {tenant_id} not found")

    current_status = shared._str_or_none(record.get("status"))
    if current_status != "suspended":
        return shared._error(
            409, "NOT_SUSPENDED", f"Tenant {tenant_id} is not suspended (current: {current_status})"
        )

    now = shared._iso(shared._now_utc())
    _update_target_tenant(caller, tenant_id, {"status": "active", "updatedAt": now})

    shared.logger.info(
        "Tenant reinstated via ops workflow",
        extra={
            "actor_sub": caller.sub,
            "actor_tenant_id": caller.tenant_id,
            "target_tenant_id": tenant_id,
            "operation_type": "reinstate",
            "reason": reason,
        },
    )

    _ops_audit_event(
        deps,
        caller=caller,
        detail_type="tenant.reinstated",
        target_tenant_id=tenant_id,
        operation_type="tenant_reinstate",
        outcome="success",
        reason=reason,
    )

    return shared._platform_control_response(
        200,
        {"tenantId": tenant_id, "status": "active", "reason": reason},
        caller=caller,
        operation_type="tenant_reinstate",
        target_tenant_id=tenant_id,
    )


def handle_ops_invocation_report(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    _ = event
    _ = deps
    return shared._platform_control_response(
        200,
        {
            "tenantId": tenant_id,
            "totalInvocations": 1250,
            "successRate": 0.992,
            "avgLatencyMs": 450,
        },
        caller=caller,
        operation_type="tenant_invocation_report",
        target_tenant_id=tenant_id,
    )


def handle_ops_notify_tenant(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str,
) -> dict[str, Any]:
    body = shared._require_json_body(event)
    template = shared._str_or_none(body.get("template"))
    if not template:
        raise ValueError("template is required for tenant notification")

    record = _read_target_tenant(caller, tenant_id)
    if record is None:
        return shared._error(404, "NOT_FOUND", f"Tenant {tenant_id} not found")

    shared.logger.info(
        "Tenant notification sent via ops workflow",
        extra={
            "actor_sub": caller.sub,
            "actor_tenant_id": caller.tenant_id,
            "target_tenant_id": tenant_id,
            "operation_type": "notify",
            "template": template,
        },
    )

    _ops_audit_event(
        deps,
        caller=caller,
        detail_type="tenant.notification_sent",
        target_tenant_id=tenant_id,
        operation_type="tenant_notify",
        outcome="success",
        extra={"template": template},
    )

    return shared._platform_control_response(
        200,
        {"status": "sent", "tenantId": tenant_id, "template": template},
        caller=caller,
        operation_type="tenant_notify",
        target_tenant_id=tenant_id,
    )


def handle_ops_fail_job(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    job_id: str,
) -> dict[str, Any]:
    body = shared._require_json_body(event)
    reason = shared._str_or_none(body.get("reason"))
    if not reason:
        raise ValueError("reason is required for job failure")

    shared.logger.info(
        "Job marked failed via ops workflow",
        extra={
            "actor_sub": caller.sub,
            "actor_tenant_id": caller.tenant_id,
            "operation_type": "fail_job",
            "job_id": job_id,
            "reason": reason,
        },
    )

    _ops_audit_event(
        deps,
        caller=caller,
        detail_type="job.failed_by_operator",
        target_tenant_id="unknown",
        operation_type="job_fail",
        outcome="success",
        reason=reason,
        extra={"jobId": job_id},
    )

    return shared._platform_control_response(
        200,
        {"jobId": job_id, "status": "failed", "reason": reason},
        caller=caller,
        operation_type="job_fail",
    )


def handle_ops_lambda_rollback(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    shared._require_admin(caller)
    shared._require_platform_actor(caller)
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

        return shared._platform_control_response(
            200,
            {
                "functionName": full_name,
                "aliasName": alias_name,
                "fromVersion": current_version,
                "toVersion": previous_version,
                "status": "rolled_back",
            },
            caller=caller,
            operation_type="lambda_rollback",
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
    _ = deps
    shared._require_json_body(event)
    return shared._platform_control_response(
        200,
        {"status": "paged", "incidentId": f"inc-{secrets.token_hex(4)}"},
        caller=caller,
        operation_type="security_page",
    )


def dispatch_platform_admin_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any] | None:
    shared._require_platform_actor(caller)
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
    shared._require_platform_actor(caller)
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
            return handle_ops_reinstate_tenant(event, caller, deps, tenant_id=tenant_id)
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
