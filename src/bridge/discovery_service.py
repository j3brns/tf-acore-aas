from __future__ import annotations

import json
from typing import Any

from boto3.dynamodb.conditions import Key
from data_access import TenantScopedDynamoDB, TenantScopedS3
from data_access.models import (
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
    return {
        "agentName": str(item.get("agent_name", "")),
        "latestVersion": str(item.get("version", "")),
        "tierMinimum": str(item.get("tier_minimum", TenantTier.BASIC.value)),
        "invocationMode": str(item.get("invocation_mode", InvocationMode.SYNC.value)),
        "streamingEnabled": bool(item.get("streaming_enabled", False)),
        "estimatedDurationSeconds": item.get("estimated_duration_seconds"),
        "ownerTeam": str(item.get("owner_team", "")),
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
    db_factory: Any = TenantScopedDynamoDB,
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
        summaries.append(_agent_summary_from_item(item))

    summaries.sort(key=lambda summary: str(summary["agentName"]))
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"items": summaries}),
    }


def get_agent_detail(
    path_params: dict[str, Any],
    request_id: str,
    *,
    agents_table: str,
    get_dynamodb: Any,
    error_response: Any,
) -> dict[str, Any]:
    agent_name = _coerce_optional_string(path_params.get("agentName"))
    if not agent_name:
        return error_response(400, "INVALID_REQUEST", "Missing agentName in path", request_id)

    ddb = get_dynamodb()
    table = ddb.Table(agents_table)
    response = table.query(KeyConditionExpression=Key("PK").eq(f"AGENT#{agent_name}"))
    items = response.get("Items", [])

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
    detail["versions"] = [
        {
            "version": str(item.get("version", "")),
            "deployedAt": str(item.get("deployed_at", "")),
            "invocationMode": str(item.get("invocation_mode", InvocationMode.SYNC.value)),
            "streamingEnabled": bool(item.get("streaming_enabled", False)),
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
