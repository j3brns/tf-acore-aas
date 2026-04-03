from __future__ import annotations

import json
from typing import Any

from data_access import ControlPlaneDynamoDB, TenantScopedDynamoDB, TenantScopedS3
from data_access.models import (
    AgentRecord,
    AgUiTransport,
    InvocationMode,
    JobStatus,
    TenantContext,
    TenantTier,
    is_invokable_agent_status,
)


def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _agent_summary_from_item(item: dict[str, Any]) -> dict[str, Any]:
    agent_name = str(item.get("agent_name", ""))
    return {
        "agentName": agent_name,
        "latestVersion": str(item.get("version", "")),
        "tierMinimum": str(item.get("tier_minimum", TenantTier.BASIC.value)),
        "invocationMode": str(item.get("invocation_mode", InvocationMode.SYNC.value)),
        "streamingEnabled": bool(item.get("streaming_enabled", False)),
        "estimatedDurationSeconds": item.get("estimated_duration_seconds"),
        "ownerTeam": str(item.get("owner_team", "")),
        "agUi": {
            "enabled": bool(item.get("ag_ui_enabled", False)),
            "transport": str(item.get("ag_ui_transport", "sse")),
            "bootstrapPath": f"/v1/agents/{agent_name}/bootstrap",
        },
    }


def _semver_sort_key(version: str) -> tuple[int, ...]:
    parts = version.split(".")
    key: list[int] = []
    for part in parts:
        digits = "".join(ch for ch in part if ch.isdigit())
        key.append(int(digits) if digits else 0)
    return tuple(key)


def _agent_record_sort_key(item: dict[str, Any]) -> tuple[tuple[int, ...], str]:
    return (
        _semver_sort_key(str(item.get("version", ""))),
        str(item.get("deployed_at", "")),
    )


def _job_key(tenant_id: str, job_id: str) -> dict[str, str]:
    return {"PK": f"TENANT#{tenant_id}", "SK": f"JOB#{job_id}"}


def _presigned_result_url(
    tenant_context: TenantContext,
    result_s3_key: str,
    *,
    job_results_bucket: str | None,
    expiry_seconds: int,
) -> str:
    bucket = _coerce_optional_string(job_results_bucket)
    if bucket is None:
        raise ValueError("JOB_RESULTS_BUCKET is not configured")

    tenant_s3 = TenantScopedS3(tenant_context)
    return tenant_s3.generate_presigned_url(
        bucket,
        result_s3_key,
        expires_in=max(1, expiry_seconds),
    )


def list_agents(
    tenant_context: TenantContext,
    *,
    agents_table: str,
    db_factory: Any = ControlPlaneDynamoDB,
    capability_policy: Any = None,
) -> dict[str, Any]:
    db = db_factory(tenant_context)
    items = db.scan_all(agents_table)

    latest_by_name: dict[str, dict[str, Any]] = {}
    for item in items:
        if not is_invokable_agent_status(_coerce_optional_string(item.get("status"))):
            continue
        agent_name = _coerce_optional_string(item.get("agent_name"))
        if agent_name is None:
            continue
        existing = latest_by_name.get(agent_name)
        if existing is None or _agent_record_sort_key(item) > _agent_record_sort_key(existing):
            latest_by_name[agent_name] = item

    tier_order = {TenantTier.BASIC: 0, TenantTier.STANDARD: 1, TenantTier.PREMIUM: 2}
    caller_tier_rank = tier_order.get(tenant_context.tier, 0)

    summaries: list[dict[str, Any]] = []
    for item in latest_by_name.values():
        tier_minimum_text = str(item.get("tier_minimum", TenantTier.BASIC.value)).lower()
        try:
            tier_minimum = TenantTier(tier_minimum_text)
        except ValueError:
            tier_minimum = TenantTier.BASIC
        if caller_tier_rank < tier_order[tier_minimum]:
            continue
        summary = _agent_summary_from_item(item)
        if capability_policy is not None and summary["agUi"]["enabled"]:
            ag_ui_enabled = capability_policy.is_enabled(
                "agents.ag_ui",
                tenant_id=tenant_context.tenant_id,
                tenant_tier=tenant_context.tier,
            ) and capability_policy.is_enabled(
                f"agents.{summary['agentName']}.ag_ui",
                tenant_id=tenant_context.tenant_id,
                tenant_tier=tenant_context.tier,
            )
            if not ag_ui_enabled:
                summary["agUi"] = {"enabled": False}
        summaries.append(summary)

    summaries.sort(key=lambda summary: str(summary["agentName"]))
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"items": summaries}),
    }


def resolve_agent_record(
    control_plane_db: ControlPlaneDynamoDB,
    *,
    agents_table: str,
    agent_name: str,
    agent_version: str | None = None,
) -> AgentRecord | None:
    """Fetch a specific agent version or the latest promoted version."""

    if agent_version:
        item = control_plane_db.get_item(
            agents_table, {"PK": f"AGENT#{agent_name}", "SK": f"VERSION#{agent_version}"}
        )
        if item and is_invokable_agent_status(_coerce_optional_string(item.get("status"))):
            try:
                return _agent_record_from_item(item)
            except ValueError:
                return None
        return None

    # Query for all versions and pick latest promoted
    response = control_plane_db.query(agents_table, pk_value=f"AGENT#{agent_name}")
    items = response.items

    promoted_items = []
    for item in items:
        if is_invokable_agent_status(_coerce_optional_string(item.get("status"))):
            promoted_items.append(item)

    if not promoted_items:
        return None

    sorted_items = sorted(promoted_items, key=_agent_record_sort_key, reverse=True)
    for item in sorted_items:
        try:
            return _agent_record_from_item(item)
        except ValueError:
            continue
    return None


def _agent_record_from_item(item: dict[str, Any]) -> AgentRecord:
    from data_access.models import AgentAgUiConfig, AgentRecord, AgentStatus

    runtime_arn = _coerce_optional_string(item.get("runtime_arn"))
    layer_hash = str(item.get("layer_hash", "")).strip()
    layer_s3_key = str(item.get("layer_s3_key", "")).strip()
    if runtime_arn is None and (layer_hash == "" or layer_s3_key == ""):
        raise ValueError("Incomplete zip-agent layer metadata")

    ag_ui_item = item.get("ag_ui", {})
    return AgentRecord(
        agent_name=str(item.get("agent_name", "")),
        version=str(item.get("version", "")),
        owner_team=str(item.get("owner_team", "")),
        tier_minimum=TenantTier(str(item.get("tier_minimum", TenantTier.BASIC.value))),
        layer_hash=layer_hash,
        layer_s3_key=layer_s3_key,
        script_s3_key=str(item.get("script_s3_key", "")),
        deployed_at=str(item.get("deployed_at", "")),
        invocation_mode=InvocationMode(str(item.get("invocation_mode", InvocationMode.SYNC.value))),
        streaming_enabled=bool(item.get("streaming_enabled", False)),
        status=AgentStatus(str(item.get("status", AgentStatus.PROMOTED.value))),
        runtime_arn=runtime_arn,
        estimated_duration_seconds=item.get("estimated_duration_seconds"),
        ag_ui=AgentAgUiConfig(
            enabled=bool(ag_ui_item.get("enabled", False)),
            transport=AgUiTransport(str(ag_ui_item.get("transport", AgUiTransport.SSE.value))),
            endpoint=_coerce_optional_string(ag_ui_item.get("endpoint")),
        )
        if isinstance(ag_ui_item, dict)
        else AgentAgUiConfig(),
    )


def get_agent_detail(
    path_params: dict[str, Any],
    request_id: str,
    *,
    agents_table: str,
    db_factory: Any = ControlPlaneDynamoDB,
    error_response: Any,
    tenant_context: TenantContext | None = None,
    capability_policy: Any = None,
) -> dict[str, Any]:
    agent_name = _coerce_optional_string(path_params.get("agentName"))
    if not agent_name:
        return error_response(400, "INVALID_REQUEST", "Missing agentName in path", request_id)

    platform_context = TenantContext(
        tenant_id="platform",
        app_id="bridge-discovery",
        tier=TenantTier.PREMIUM,
        sub="bridge-discovery",
    )
    db = db_factory(platform_context)
    response = db.query(agents_table, pk_value=f"AGENT#{agent_name}")
    items = response.items

    promoted_items = []
    for item in items:
        item_status = _coerce_optional_string(item.get("status"))
        if is_invokable_agent_status(item_status):
            promoted_items.append(item)
    if not promoted_items:
        return error_response(404, "NOT_FOUND", f"Agent '{agent_name}' not found", request_id)

    sorted_items = sorted(promoted_items, key=_agent_record_sort_key, reverse=True)
    latest = sorted_items[0]
    detail = _agent_summary_from_item(latest)
    if capability_policy is not None and tenant_context is not None and detail["agUi"]["enabled"]:
        ag_ui_enabled = capability_policy.is_enabled(
            "agents.ag_ui",
            tenant_id=tenant_context.tenant_id,
            tenant_tier=tenant_context.tier,
        ) and capability_policy.is_enabled(
            f"agents.{detail['agentName']}.ag_ui",
            tenant_id=tenant_context.tenant_id,
            tenant_tier=tenant_context.tier,
        )
        if not ag_ui_enabled:
            detail["agUi"] = {"enabled": False}
    detail["versions"] = [
        {
            "version": str(item.get("version", "")),
            "deployedAt": str(item.get("deployed_at", "")),
            "invocationMode": str(item.get("invocation_mode", InvocationMode.SYNC.value)),
            "streamingEnabled": bool(item.get("streaming_enabled", False)),
            "agUi": {
                "enabled": bool(item.get("ag_ui_enabled", False)),
                "transport": str(item.get("ag_ui_transport", "sse")),
            },
        }
        for item in sorted_items
    ]

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(detail),
    }


def get_job_status(
    tenant_context: TenantContext,
    path_params: dict[str, Any],
    request_id: str,
    *,
    jobs_table: str,
    job_results_bucket: str | None,
    job_result_url_expiry_seconds: int,
    error_response: Any,
    db_factory: Any = TenantScopedDynamoDB,
) -> dict[str, Any]:
    job_id = _coerce_optional_string(path_params.get("jobId"))
    if not job_id:
        return error_response(400, "INVALID_REQUEST", "Missing jobId in path", request_id)

    db = db_factory(tenant_context)
    record = db.get_item(jobs_table, _job_key(tenant_context.tenant_id, job_id))

    if record is None:
        return error_response(404, "NOT_FOUND", f"Job '{job_id}' not found", request_id)

    result_url: str | None = None
    status = str(record.get("status", JobStatus.PENDING))
    result_key = _coerce_optional_string(record.get("result_s3_key"))
    if status == str(JobStatus.COMPLETED) and result_key:
        try:
            result_url = _presigned_result_url(
                tenant_context,
                result_key,
                job_results_bucket=job_results_bucket,
                expiry_seconds=job_result_url_expiry_seconds,
            )
        except ValueError as exc:
            return error_response(500, "INTERNAL_ERROR", str(exc), request_id)
        except Exception:
            return error_response(
                500, "INTERNAL_ERROR", "Failed to generate result URL", request_id
            )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "jobId": str(record.get("job_id", job_id)),
                "tenantId": str(record.get("tenant_id", tenant_context.tenant_id)),
                "agentName": str(record.get("agent_name", "")),
                "status": status,
                "createdAt": str(record.get("created_at", "")),
                "startedAt": _coerce_optional_string(record.get("started_at")),
                "completedAt": _coerce_optional_string(record.get("completed_at")),
                "resultUrl": result_url,
                "errorMessage": _coerce_optional_string(record.get("error_message")),
                "webhookDelivered": bool(record.get("webhook_delivered", False)),
                "webhookUrl": _coerce_optional_string(record.get("webhook_url")),
                "webhookDeliveryStatus": _coerce_optional_string(
                    record.get("webhook_delivery_status")
                ),
                "webhookDeliveryAttempts": int(record.get("webhook_delivery_attempts", 0)),
                "webhookDeliveryError": _coerce_optional_string(
                    record.get("webhook_delivery_error")
                ),
            }
        ),
    }
