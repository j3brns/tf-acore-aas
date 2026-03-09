"""
async_runner.handler — Async job tracking Lambda.

Tracks async AgentCore sessions by polling Runtime /ping and updating JOB records.
This function is event-driven (not SQS-triggered) and enforces ADR-010 semantics:
HealthyBusy means work is still running; Healthy means work is complete.

Implemented in TASK-046.
ADRs: ADR-010
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext
from data_access import TenantScopedDynamoDB
from data_access.models import JobStatus, TenantContext, TenantTier

logger = Logger(service="async-runner")
tracer = Tracer()

JOBS_TABLE = os.environ.get("JOBS_TABLE", "platform-jobs")
RUNTIME_PING_URL = os.environ.get("RUNTIME_PING_URL") or os.environ.get("MOCK_RUNTIME_URL")
RUNTIME_PING_TIMEOUT_SECONDS = float(os.environ.get("RUNTIME_PING_TIMEOUT_SECONDS", "2.0"))


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Process one async job status poll event."""
    del context  # Not used by this handler implementation.

    payload = _payload(event)

    try:
        job_id = _required_str(payload, "jobId")
        tenant_id = _required_str(payload, "tenantId")
        app_id = _required_str(payload, "appId")
        session_id = _required_str(payload, "sessionId")
        agent_name = _required_str(payload, "agentName")
    except ValueError as exc:
        return _error_response(400, "INVALID_REQUEST", str(exc))

    logger.append_keys(
        tenantid=tenant_id,
        appid=app_id,
        jobid=job_id,
        sessionid=session_id,
    )

    tier = _tenant_tier(payload.get("tier"))
    tenant_context = TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=tier,
        sub="async-runner",
    )
    db = TenantScopedDynamoDB(tenant_context)
    key = {"PK": f"TENANT#{tenant_id}", "SK": f"JOB#{job_id}"}
    job = db.get_item(JOBS_TABLE, key)
    if job is None:
        return _error_response(404, "NOT_FOUND", f"Job '{job_id}' not found")

    current_status = str(job.get("status", "pending")).lower()
    if current_status in {str(JobStatus.COMPLETED), str(JobStatus.FAILED)}:
        return _response(
            200,
            {
                "jobId": job_id,
                "status": current_status,
                "runtimeStatus": None,
                "checkedAt": _utc_now_iso(),
            },
        )

    ping_url = _ping_url(payload.get("runtimePingUrl"))
    if ping_url is None:
        return _error_response(503, "SERVICE_UNAVAILABLE", "RUNTIME_PING_URL is not configured")

    headers = {
        "x-tenant-id": tenant_id,
        "x-app-id": app_id,
        "x-session-id": session_id,
        "x-agent-name": agent_name,
    }

    try:
        ping_payload = _http_get_json(
            ping_url,
            headers=headers,
            timeout_seconds=RUNTIME_PING_TIMEOUT_SECONDS,
        )
    except urllib.error.URLError:
        logger.exception("Runtime /ping request failed")
        return _error_response(
            502,
            "BAD_GATEWAY",
            "Failed to query runtime /ping",
        )
    except ValueError as exc:
        return _error_response(502, "BAD_GATEWAY", str(exc))

    runtime_status = str(ping_payload.get("status", "")).strip()
    now_iso = _utc_now_iso()

    if runtime_status == "HealthyBusy":
        _update_running(db, key, now_iso)
        next_status = str(JobStatus.RUNNING)
    elif runtime_status == "Healthy":
        _update_completed(db, key, now_iso)
        next_status = str(JobStatus.COMPLETED)
    else:
        _update_failed(
            db,
            key,
            now_iso,
            f"Unexpected runtime ping status: {runtime_status or 'empty'}",
        )
        next_status = str(JobStatus.FAILED)

    return _response(
        200,
        {
            "jobId": job_id,
            "status": next_status,
            "runtimeStatus": runtime_status or None,
            "checkedAt": now_iso,
        },
    )


def _update_running(db: TenantScopedDynamoDB, key: dict[str, str], now_iso: str) -> None:
    db.update_item(
        JOBS_TABLE,
        key,
        update_expression="SET #status = :status, started_at = if_not_exists(started_at, :now)",
        expression_attribute_values={
            ":status": str(JobStatus.RUNNING),
            ":now": now_iso,
        },
        expression_attribute_names={"#status": "status"},
    )


def _update_completed(db: TenantScopedDynamoDB, key: dict[str, str], now_iso: str) -> None:
    db.update_item(
        JOBS_TABLE,
        key,
        update_expression="SET #status = :status, completed_at = :now",
        expression_attribute_values={
            ":status": str(JobStatus.COMPLETED),
            ":now": now_iso,
        },
        expression_attribute_names={"#status": "status"},
    )


def _update_failed(
    db: TenantScopedDynamoDB,
    key: dict[str, str],
    now_iso: str,
    reason: str,
) -> None:
    db.update_item(
        JOBS_TABLE,
        key,
        update_expression=(
            "SET #status = :status, "
            "completed_at = if_not_exists(completed_at, :now), "
            "error_message = :reason"
        ),
        expression_attribute_values={
            ":status": str(JobStatus.FAILED),
            ":now": now_iso,
            ":reason": reason,
        },
        expression_attribute_names={"#status": "status"},
    )


def _http_get_json(
    url: str,
    *,
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    request = urllib.request.Request(url=url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw_payload = response.read().decode("utf-8")

    parsed = json.loads(raw_payload) if raw_payload else {}
    if not isinstance(parsed, dict):
        raise ValueError("Runtime /ping response must be a JSON object")
    return parsed


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail")
    if isinstance(detail, dict):
        return detail
    return event


def _required_str(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"{field} is required")


def _tenant_tier(raw_tier: Any) -> TenantTier:
    if raw_tier is None:
        return TenantTier.BASIC
    try:
        return TenantTier(str(raw_tier))
    except ValueError:
        logger.warning("Invalid tier supplied in event; defaulting to basic", tier=raw_tier)
        return TenantTier.BASIC


def _ping_url(event_url: Any) -> str | None:
    base = event_url if isinstance(event_url, str) and event_url.strip() else RUNTIME_PING_URL
    if not base:
        return None
    if base.rstrip("/").endswith("/ping"):
        return base
    return f"{base.rstrip('/')}/ping"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _error_response(status_code: int, code: str, message: str) -> dict[str, Any]:
    return _response(status_code, {"error": {"code": code, "message": message}})
