from __future__ import annotations

import json
import time
import uuid
from typing import Any

from botocore.exceptions import ClientError
from data_access.models import AgentRecord, InvocationMode, InvocationStatus, TenantContext

from src.platform_utils import coerce_optional_string as _coerce_optional_string
from src.platform_utils import get_hex_jitter as _get_hex_jitter


def coerce_optional_string(val: Any) -> str | None:
    return _coerce_optional_string(val)


def get_jitter() -> str:
    return _get_hex_jitter()


def validate_execution_role_arn(role_arn: str, expected_account_id: str, pattern: Any) -> str:
    match = pattern.fullmatch(role_arn)
    if not match:
        raise ValueError("Tenant execution role ARN is malformed")
    if match.group("account_id") != expected_account_id:
        raise ValueError("Tenant execution role ARN account mismatch")
    return role_arn


def build_runtime_payload(
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "prompt": prompt,
        "input": prompt,
        "mode": agent.invocation_mode.value,
        "appid": tenant_context.app_id,
        "tenantId": tenant_context.tenant_id,
        "agentName": agent.agent_name,
        "agentVersion": agent.version,
    }
    if session_id:
        payload["sessionId"] = session_id
    return payload


def validate_runtime_arn(runtime_arn: str, pattern: Any) -> Any:
    match = pattern.fullmatch(runtime_arn)
    if not match:
        raise ValueError("Agent runtime ARN is malformed")
    return match


def runtime_failure_response(
    tenant_context: TenantContext,
    agent: AgentRecord,
    invocation_id: str,
    start_time: float,
    invocation_mode: InvocationMode,
    runtime_region: str,
    request_id: str,
    exc: Exception,
    *,
    session_id: str | None,
    emit_bedrock_throttle_metric: Any,
    log_invocation: Any,
    error_response: Any,
) -> dict[str, Any]:
    status_code = 502
    error_code = "RUNTIME_INVOCATION_FAILED"
    message = "Agent runtime invocation failed"

    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        error_code = str(err.get("Code") or error_code)
        message = str(err.get("Message") or message)
        status_code = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 502))
        if error_code == "ThrottlingException":
            emit_bedrock_throttle_metric(
                tenant_context=tenant_context,
                agent=agent,
                runtime_region=runtime_region,
            )
    else:
        message = str(exc) or message

    latency_ms = int((time.time() - start_time) * 1000)
    log_invocation(
        tenant_context,
        agent,
        invocation_id,
        InvocationStatus.ERROR,
        latency_ms,
        invocation_mode,
        session_id=session_id,
        error_code=error_code,
        runtime_region=runtime_region,
    )
    return error_response(status_code, error_code, message, request_id)


def invoke_real_runtime(
    region: str,
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any | None = None,
    invocation_id: str | None = None,
    start_time: float | None = None,
    runtime_credentials: dict[str, Any] | None = None,
    *,
    coerce_optional_string: Any,
    validate_runtime_arn: Any,
    get_tenant_record: Any,
    resolve_tenant_execution_role: Any,
    get_ssm: Any,
    validate_execution_role_arn: Any,
    get_sts: Any,
    assume_tenant_role: Any,
    get_runtime_client: Any,
    build_runtime_payload: Any,
    log_invocation: Any,
    runtime_failure_response: Any,
    error_response: Any,
) -> Any:
    del webhook_id
    del response_stream

    runtime_arn = coerce_optional_string(agent.runtime_arn)
    if not runtime_arn:
        return error_response(
            500, "INVALID_RUNTIME", "Agent runtime ARN not configured", request_id
        )
    runtime_arn_match = validate_runtime_arn(runtime_arn)
    runtime_arn_region = runtime_arn_match.group("region")
    if runtime_arn_region != region:
        runtime_arn = runtime_arn.replace(f":{runtime_arn_region}:", f":{region}:", 1)
    invocation_id = invocation_id or str(uuid.uuid4())
    start_time = start_time or time.time()

    if not runtime_credentials:
        tenant_record = get_tenant_record(tenant_context)
        if not tenant_record:
            return error_response(404, "NOT_FOUND", "Tenant metadata not found", request_id)

        role_arn = coerce_optional_string(
            tenant_record.get("executionRoleArn") or tenant_record.get("execution_role_arn")
        )
        if not role_arn:
            role_arn = resolve_tenant_execution_role(get_ssm(), tenant_id=tenant_context.tenant_id)
        if not role_arn:
            return error_response(
                500, "INVALID_RUNTIME", "Tenant execution role ARN not configured", request_id
            )

        expected_account_id = (
            coerce_optional_string(
                tenant_record.get("accountId") or tenant_record.get("account_id")
            )
            or ""
        )
        if expected_account_id:
            try:
                role_arn = validate_execution_role_arn(role_arn, expected_account_id)
            except ValueError as exc:
                return error_response(500, "INVALID_RUNTIME", str(exc), request_id)

        runtime_credentials = assume_tenant_role(
            get_sts(), role_arn=role_arn, session_name=f"invoke-{invocation_id[:8]}"
        )

    try:
        runtime_client = get_runtime_client(region, credentials=runtime_credentials)
        runtime_response = runtime_client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            payload=json.dumps(
                build_runtime_payload(agent, tenant_context, prompt, session_id=session_id)
            ).encode("utf-8"),
        )
        response_body = runtime_response.get("response")
        if hasattr(response_body, "read"):
            body_bytes = response_body.read()
        else:
            body_bytes = response_body if isinstance(response_body, (bytes, bytearray)) else b""
        latency_ms = int((time.time() - start_time) * 1000)
        log_invocation(
            tenant_context,
            agent,
            invocation_id,
            InvocationStatus.SUCCESS,
            latency_ms,
            agent.invocation_mode,
            session_id=session_id,
            runtime_region=region,
        )
        headers = {"Content-Type": str(runtime_response.get("contentType", "application/json"))}
        runtime_session_id = coerce_optional_string(runtime_response.get("runtimeSessionId"))
        if runtime_session_id:
            headers["x-runtime-session-id"] = runtime_session_id
        return {
            "statusCode": int(runtime_response.get("statusCode", 200)),
            "headers": headers,
            "body": (
                body_bytes.decode("utf-8") if isinstance(body_bytes, (bytes, bytearray)) else ""
            ),
        }
    except Exception as exc:
        return runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            region,
            request_id,
            exc,
            session_id=session_id,
        )


def invoke_mock_runtime(
    url: str,
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any | None = None,
    invocation_id: str | None = None,
    start_time: float | None = None,
    *,
    get_http_session: Any,
    build_runtime_payload: Any,
    log_invocation: Any,
    runtime_failure_response: Any,
) -> dict[str, Any] | None:
    del webhook_id
    del response_stream

    invocation_id = invocation_id or str(uuid.uuid4())
    start_time = start_time or time.time()
    try:
        response = get_http_session().post(
            url.rstrip("/"),
            json=build_runtime_payload(agent, tenant_context, prompt, session_id=session_id),
            timeout=5,
        )
        latency_ms = int((time.time() - start_time) * 1000)
        status = InvocationStatus.SUCCESS if response.ok else InvocationStatus.ERROR
        log_invocation(
            tenant_context,
            agent,
            invocation_id,
            status,
            latency_ms,
            agent.invocation_mode,
            session_id=session_id,
            runtime_region="mock-runtime",
        )
        return {
            "statusCode": response.status_code,
            "headers": {"Content-Type": response.headers.get("Content-Type", "application/json")},
            "body": response.text,
        }
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
