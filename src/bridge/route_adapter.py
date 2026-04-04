from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from data_access import ControlPlaneDynamoDB, TenantScopedDynamoDB
from data_access.models import (
    AgentRecord,
    InvocationMode,
    InvocationStatus,
    JobStatus,
    TenantContext,
)

from src.bridge.constants import (
    BFF_SESSION_KEEPALIVE_PATH,
    BFF_TOKEN_REFRESH_PATH,
    JOBS_TABLE,
    SESSIONS_TABLE,
)


def send_streaming_response(
    response_stream: Any,
    status_code: int,
    body: bytes,
    headers: dict[str, str],
) -> None:
    preamble = json.dumps({"statusCode": status_code, "headers": headers}).encode("utf-8") + b"\0"
    response_stream.write(preamble)
    if body:
        response_stream.write(body)


def handle_streaming_invocation(
    *,
    url: str,
    payload: dict[str, Any],
    agent: AgentRecord,
    tenant_context: TenantContext,
    invocation_id: str,
    start_time: float,
    response_stream: Any | None,
    request_id: str,
    session_id: str | None,
    get_http_session: Any,
    log_invocation: Any,
    runtime_failure_response: Any,
    error_response: Any,
) -> dict[str, Any] | None:
    if response_stream is None:
        return error_response(
            500,
            "INTERNAL_ERROR",
            "Streaming invocation requires a response stream",
            request_id,
        )

    try:
        response = get_http_session().post(url.rstrip("/"), json=payload, stream=True, timeout=5)
        send_streaming_response(
            response_stream,
            200,
            b"",
            {"Content-Type": "text/event-stream"},
        )
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            response_stream.write(raw_line + b"\n\n")
        latency_ms = int((time.time() - start_time) * 1000)
        log_invocation(
            tenant_context,
            agent,
            invocation_id,
            InvocationStatus.SUCCESS,
            latency_ms,
            agent.invocation_mode,
            session_id=session_id,
            runtime_region="mock-runtime",
        )
        return None
    except Exception as exc:
        return runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            "mock-runtime",
            request_id,
            exc,
            session_id=session_id,
        )


def mock_runtime_response_body(response: Any, session_id: str | None) -> tuple[str, str | None]:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text, session_id

    runtime_session_id = session_id
    parts: list[str] = []
    for raw_line in response.iter_lines():
        if not raw_line:
            continue
        line = (
            raw_line.decode("utf-8") if isinstance(raw_line, (bytes, bytearray)) else str(raw_line)
        )
        payload = line[5:].strip() if line.startswith("data:") else line.strip()
        if payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if data.get("type") == "session" and data.get("sessionId"):
            runtime_session_id = str(data["sessionId"])
        if data.get("type") == "text":
            parts.append(str(data.get("content", "")))

    body = {
        "output": "".join(parts),
        "usage": {"inputTokens": 0, "outputTokens": 0},
    }
    if runtime_session_id:
        body["sessionId"] = runtime_session_id
    return json.dumps(body), runtime_session_id


def is_runtime_unavailable_error(exc: Exception) -> bool:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "")) == "ServiceUnavailableException"
    return isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError))


def invoke_agent(
    *,
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any | None,
    get_config: Any,
    coerce_optional_string: Any,
    get_webhook_registration: Any,
    error_response: Any,
    build_runtime_payload: Any,
    get_http_session: Any,
    invoke_mock_runtime: Any,
    handle_streaming_invocation: Any,
    log_invocation: Any,
    invoke_real_runtime: Any,
    runtime_failure_response: Any,
    trigger_failover: Any,
) -> Any:
    config = get_config()
    mock_url = coerce_optional_string(config.get("mock_runtime_url"))
    runtime_region = str(config["runtime_region"])
    invocation_id = str(uuid.uuid4())
    start_time = time.time()

    if agent.invocation_mode == InvocationMode.ASYNC:
        webhook_record = None
        if webhook_id:
            webhook_record = get_webhook_registration(tenant_context, webhook_id)
            if webhook_record is None:
                return error_response(
                    404,
                    "NOT_FOUND",
                    f"Webhook '{webhook_id}' not found",
                    request_id,
                )

        job_id = str(uuid.uuid4())
        TenantScopedDynamoDB(tenant_context).put_item(
            JOBS_TABLE,
            {
                "PK": f"TENANT#{tenant_context.tenant_id}",
                "SK": f"JOB#{job_id}",
                "job_id": job_id,
                "tenant_id": tenant_context.tenant_id,
                "app_id": tenant_context.app_id,
                "agent_name": agent.agent_name,
                "status": JobStatus.PENDING.value,
                "created_at": datetime.now(UTC).isoformat(),
                "webhook_id": webhook_id,
                "webhook_url": coerce_optional_string(
                    webhook_record.get("callback_url") if webhook_record else None
                ),
            },
        )
        return {
            "statusCode": 202,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "status": "accepted",
                    "jobId": job_id,
                    "webhookDelivery": "registered" if webhook_record else "none",
                }
            ),
        }

    if mock_url:
        if response_stream is not None and agent.invocation_mode == InvocationMode.STREAMING:
            return handle_streaming_invocation(
                url=mock_url,
                payload=build_runtime_payload(
                    agent,
                    tenant_context,
                    prompt,
                    session_id=session_id,
                ),
                agent=agent,
                tenant_context=tenant_context,
                invocation_id=invocation_id,
                start_time=start_time,
                response_stream=response_stream,
                request_id=request_id,
                session_id=session_id,
                get_http_session=get_http_session,
                log_invocation=log_invocation,
                runtime_failure_response=runtime_failure_response,
                error_response=error_response,
            )
        response = invoke_mock_runtime(
            mock_url,
            agent,
            tenant_context,
            prompt,
            session_id,
            webhook_id,
            request_id,
            response_stream,
            invocation_id,
            start_time,
        )
        if response is not None and response.get("statusCode") == 200:
            mock_response = get_http_session().post(mock_url.rstrip("/"))
            body_text, resolved_session_id = mock_runtime_response_body(mock_response, session_id)
            response["body"] = body_text
            if resolved_session_id:
                latency_ms = int((time.time() - start_time) * 1000)
                log_invocation(
                    tenant_context,
                    agent,
                    invocation_id,
                    InvocationStatus.SUCCESS,
                    latency_ms,
                    agent.invocation_mode,
                    session_id=resolved_session_id,
                    runtime_region="mock-runtime",
                )
        return response

    try:
        return invoke_real_runtime(
            runtime_region,
            agent,
            tenant_context,
            prompt,
            session_id,
            webhook_id,
            request_id,
            response_stream,
            invocation_id,
            start_time,
        )
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        if error_code == "ThrottlingException":
            latency_ms = int((time.time() - start_time) * 1000)
            log_invocation(
                tenant_context,
                agent,
                invocation_id,
                InvocationStatus.ERROR,
                latency_ms,
                agent.invocation_mode,
                session_id=session_id,
                error_code="THROTTLED",
                runtime_region=runtime_region,
            )
            response = error_response(429, "THROTTLED", "Agent runtime throttled", request_id)
            response["headers"]["Retry-After"] = "1"
            return response
        if is_runtime_unavailable_error(exc):
            new_region = trigger_failover(runtime_region)
            return invoke_real_runtime(
                new_region,
                agent,
                tenant_context,
                prompt,
                session_id,
                webhook_id,
                request_id,
                response_stream,
                invocation_id,
                start_time,
            )
        return runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            runtime_region,
            request_id,
            exc,
            session_id=session_id,
        )
    except Exception as exc:
        if is_runtime_unavailable_error(exc):
            new_region = trigger_failover(runtime_region)
            return invoke_real_runtime(
                new_region,
                agent,
                tenant_context,
                prompt,
                session_id,
                webhook_id,
                request_id,
                response_stream,
                invocation_id,
                start_time,
            )
        return runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            runtime_region,
            request_id,
            exc,
            session_id=session_id,
        )


def normalize_contract_path(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[0] != "v1" and parts[1] == "v1":
        parts = parts[1:]
    return "/" + "/".join(parts)


def is_job_contract_path(path: str, job_id: str | None) -> bool:
    if not job_id:
        return False
    return normalize_contract_path(path) == f"/v1/jobs/{job_id}"


def is_agent_detail_path(path: str, agent_name: str | None) -> bool:
    if not agent_name:
        return False
    return normalize_contract_path(path) == f"/v1/agents/{agent_name}"


def is_agents_list_path(path: str) -> bool:
    return normalize_contract_path(path) == "/v1/agents"


def is_agent_bootstrap_path(path: str, agent_name: str | None) -> bool:
    if not agent_name:
        return False
    return normalize_contract_path(path) == f"/v1/agents/{agent_name}/bootstrap"


def bootstrap_agent_session(
    *,
    agent_name: str,
    tenant_context: TenantContext,
    request_id: str,
    get_agent_record: Any,
    get_platform_context: Any,
    coerce_optional_string: Any,
    entra_audience: str | None,
    ag_ui_scope_name: str,
) -> dict[str, Any]:
    agent = get_agent_record(agent_name)
    if not agent:
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "error": {
                        "code": "NOT_FOUND",
                        "message": f"Agent '{agent_name}' not found",
                        "requestId": request_id,
                    }
                }
            ),
        }
    if not agent.ag_ui.enabled or not agent.ag_ui.endpoint:
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "error": {
                        "code": "NOT_FOUND",
                        "message": f"Agent '{agent_name}' is not AG-UI enabled",
                        "requestId": request_id,
                    }
                }
            ),
        }

    session_id = str(uuid.uuid4())
    runtime_session_id = str(uuid.uuid4())
    session_item = {
        "PK": f"TENANT#{tenant_context.tenant_id}",
        "SK": f"SESSION#{session_id}",
        "tenant_id": tenant_context.tenant_id,
        "app_id": tenant_context.app_id,
        "session_id": session_id,
        "runtime_session_id": runtime_session_id,
        "bootstrap_type": "ag_ui",
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        dynamodb_resource = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
        ControlPlaneDynamoDB(
            get_platform_context(),
            dynamodb_resource=dynamodb_resource,
        ).put_item(SESSIONS_TABLE, session_item)
        dynamodb_resource.Table(SESSIONS_TABLE).put_item(Item=session_item)
    except Exception:
        pass
    scope = f"{entra_audience}/{ag_ui_scope_name}" if entra_audience else ag_ui_scope_name
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "agentName": agent.agent_name,
                "sessionId": session_id,
                "runtimeSessionId": runtime_session_id,
                "transport": agent.ag_ui.transport.value,
                "connectUrl": agent.ag_ui.endpoint,
                "tokenRefreshPath": BFF_TOKEN_REFRESH_PATH,
                "sessionKeepalivePath": BFF_SESSION_KEEPALIVE_PATH,
                "auth": {"scopes": [scope]},
            }
        ),
    }
