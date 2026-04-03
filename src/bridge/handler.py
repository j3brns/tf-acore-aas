"""
bridge.handler — Agent invocation bridge Lambda.

Reads invocation_mode from agent registry, assumes tenant execution role,
and routes to AgentCore Runtime via sync, streaming, or async paths.

ADRs: ADR-003, ADR-005, ADR-009, ADR-010
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import requests
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from data_access import (
    TenantCapabilityClient,
    TenantScopedDynamoDB,
)
from data_access.models import (
    AgentRecord,
    InvocationMode,
    InvocationStatus,
    JobRecord,
    JobStatus,
    TenantContext,
    TenantTier,
)

from src.bridge import (
    config_provider,
    constants,
    lock_manager,
    role_resolver,
    telemetry,
)
from src.bridge.config_provider import (
    ConfigProvider,
    config_defaults,
    fetch_ssm_config,
)
from src.bridge.constants import (
    AG_UI_SCOPE_NAME,
    AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS,
    AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS,
    AGENTS_TABLE,
    IAM_ROLE_ARN_PATTERN,
    INVOCATION_TTL_SECONDS,
    INVOCATIONS_TABLE,
    JOB_RESULT_URL_EXPIRY_SECONDS,
    JOB_RESULTS_BUCKET,
    JOB_TTL_SECONDS,
    JOBS_TABLE,
    OPS_LOCKS_TABLE,
    RUNTIME_ARN_PATTERN,
    RUNTIME_REGION_PARAM,
    TENANTS_TABLE,
)

from .discovery_service import (
    get_job_status as discovery_get_job_status,
)
from .discovery_service import (
    list_agents as discovery_list_agents,
)
from .discovery_service import (
    resolve_agent_record as discovery_resolve_agent_record,
)
from .invocation_engine import handle_invoke_request
from .runtime_orchestrator import build_runtime_orchestrator

logger = Logger(service="bridge")
tracer = Tracer()


def _aws_region() -> str:
    return os.environ["AWS_REGION"]


def get_capability_client() -> TenantCapabilityClient:
    return TenantCapabilityClient()


def get_ssm() -> Any:
    return boto3.client("ssm", region_name=_aws_region())


def get_sts() -> Any:
    return boto3.client("sts", region_name=_aws_region())


def get_dynamodb() -> Any:
    return boto3.resource("dynamodb", region_name=_aws_region())


def get_cloudwatch() -> Any:
    return boto3.client("cloudwatch", region_name=_aws_region())


def get_http_session() -> requests.Session:
    return requests.Session()


def get_config(force_refresh: bool = False) -> dict[str, Any]:
    provider = ConfigProvider(
        fetcher=lambda: fetch_ssm_config(get_ssm(), get_http_session()),
        fallback_factory=config_defaults,
        ttl_seconds=60,
    )
    return provider.get(force_refresh=force_refresh)


def get_runtime_client(region: str, credentials: dict[str, Any] | None = None) -> Any:
    session_kwargs: dict[str, Any] = {"region_name": region}
    if credentials:
        session_kwargs.update(
            {
                "aws_access_key_id": credentials["AccessKeyId"],
                "aws_secret_access_key": credentials["SecretAccessKey"],
                "aws_session_token": credentials["SessionToken"],
            }
        )

    session = boto3.Session(**session_kwargs)
    client_kwargs: dict[str, Any] = {
        "service_name": "bedrock-agentcore",
        "region_name": region,
        "config": Config(
            connect_timeout=AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS,
            read_timeout=AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    }
    if os.environ.get("BEDROCK_AGENTCORE_DP_ENDPOINT"):
        client_kwargs["endpoint_url"] = os.environ.get("BEDROCK_AGENTCORE_DP_ENDPOINT")
    return session.client(**client_kwargs)


def trigger_failover(current_region: str) -> str:
    return lock_manager.trigger_failover(
        dynamodb=get_dynamodb(),
        ssm=get_ssm(),
        current_region=current_region,
        get_config_fn=get_config,
        runtime_region_param=RUNTIME_REGION_PARAM,
    )


# Backward-compatibility aliases for existing submodules/logic
_acquire_lock = lock_manager.acquire_lock
_release_lock = lock_manager.release_lock
_trigger_failover = trigger_failover
_log_invocation = telemetry.log_invocation
_emit_invocation_metrics = telemetry.emit_invocation_metrics
_emit_bedrock_throttle_metric = telemetry.emit_bedrock_throttle_metric
_log_job = telemetry.log_job
_resolve_tenant_execution_role = role_resolver.resolve_tenant_execution_role
_assume_tenant_role = role_resolver.assume_tenant_role
_AGENTS_TABLE = AGENTS_TABLE


def get_tenant_record(tenant_context: TenantContext) -> dict[str, Any] | None:
    try:
        db = TenantScopedDynamoDB(tenant_context)
        return db.get_item(
            TENANTS_TABLE, {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": "METADATA"}
        )
    except Exception:
        logger.exception("Failed to fetch tenant record")
        return None


def get_agent_record(agent_name: str, agent_version: str | None = None) -> AgentRecord | None:
    return discovery_resolve_agent_record(
        get_dynamodb(),
        agents_table=AGENTS_TABLE,
        agent_name=agent_name,
        agent_version=agent_version,
    )


def get_webhook_registration(
    tenant_context: TenantContext, webhook_id: str
) -> dict[str, Any] | None:
    try:
        db = TenantScopedDynamoDB(tenant_context)
        return db.get_item(
            TENANTS_TABLE,
            {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": f"WEBHOOK#{webhook_id}"},
        )
    except Exception:
        logger.exception("Failed to fetch webhook registration")
        return None


def error_response(status_code: int, code: str, message: str, request_id: str) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "x-amzn-RequestId": request_id},
        "body": json.dumps({"error": {"code": code, "message": message, "requestId": request_id}}),
    }


def _coerce_optional_string(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def get_jitter() -> str:
    """Return a random 2-character hex jitter for hot-partition mitigation."""
    return secrets.token_hex(1)


def _validate_execution_role_arn(role_arn: str, expected_account_id: str) -> str:
    match = IAM_ROLE_ARN_PATTERN.fullmatch(role_arn)
    if not match:
        raise ValueError("Tenant execution role ARN is malformed")
    if match.group("account_id") != expected_account_id:
        raise ValueError("Tenant execution role is in an untrusted account")
    return role_arn


def _build_runtime_payload(
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "agentName": agent.agent_name,
        "agentVersion": agent.version,
        "prompt": prompt,
        "tenantId": tenant_context.tenant_id,
        "appId": tenant_context.app_id,
    }
    if session_id:
        payload["sessionId"] = session_id
    return payload


def _validate_runtime_arn(runtime_arn: str) -> re.Match[str]:
    match = RUNTIME_ARN_PATTERN.fullmatch(runtime_arn)
    if not match:
        raise ValueError("Agent runtime ARN is malformed")
    return match


def log_invocation(
    tenant_context: TenantContext,
    agent: AgentRecord,
    invocation_id: str,
    status: InvocationStatus,
    latency_ms: int,
    mode: InvocationMode,
    input_tokens: int = 0,
    output_tokens: int = 0,
    job_id: str | None = None,
    session_id: str | None = None,
    error_code: str | None = None,
    runtime_region: str | None = None,
) -> None:
    telemetry.log_invocation(
        get_cloudwatch(),
        tenant_context,
        agent,
        invocation_id,
        status,
        latency_ms,
        mode,
        runtime_region=runtime_region or get_config()["runtime_region"],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        job_id=job_id,
        session_id=session_id,
        error_code=error_code,
        jitter=get_jitter(),
    )


def emit_invocation_metrics(
    tenant_context: TenantContext,
    agent: AgentRecord,
    status: InvocationStatus,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    telemetry.emit_invocation_metrics(
        get_cloudwatch(),
        tenant_context,
        agent,
        status,
        latency_ms,
        input_tokens,
        output_tokens,
    )


def emit_bedrock_throttle_metric(
    *,
    tenant_context: TenantContext,
    agent: AgentRecord,
    runtime_region: str,
) -> None:
    telemetry.emit_bedrock_throttle_metric(
        get_cloudwatch(),
        tenant_context=tenant_context,
        agent=agent,
        runtime_region=runtime_region,
    )


def log_job(tenant_context: TenantContext, record: JobRecord) -> None:
    telemetry.log_job(tenant_context, record)


def _runtime_failure_response(
    tenant_context: TenantContext,
    agent: AgentRecord,
    invocation_id: str,
    start_time: float,
    invocation_mode: InvocationMode,
    runtime_region: str,
    request_id: str,
    exc: Exception,
    *,
    session_id: str | None = None,
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
) -> Any:
    del webhook_id
    del response_stream

    runtime_arn = _coerce_optional_string(agent.runtime_arn)
    if not runtime_arn:
        return error_response(
            500, "INVALID_RUNTIME", "Agent runtime ARN not configured", request_id
        )
    _validate_runtime_arn(runtime_arn)
    invocation_id = invocation_id or str(uuid.uuid4())
    start_time = start_time or time.time()

    if not runtime_credentials:
        tenant_record = get_tenant_record(tenant_context)
        if not tenant_record:
            return error_response(404, "NOT_FOUND", "Tenant metadata not found", request_id)

        role_arn = _coerce_optional_string(tenant_record.get("executionRoleArn"))
        if not role_arn:
            role_arn = role_resolver.resolve_tenant_execution_role(
                get_ssm(), tenant_id=tenant_context.tenant_id
            )
        if not role_arn:
            return error_response(
                403, "FORBIDDEN", "Tenant execution role not configured", request_id
            )

        expected_account_id = _coerce_optional_string(tenant_record.get("accountId")) or ""
        if expected_account_id:
            role_arn = _validate_execution_role_arn(role_arn, expected_account_id)

        runtime_credentials = role_resolver.assume_tenant_role(
            get_sts(), role_arn=role_arn, session_name=f"invoke-{invocation_id[:8]}"
        )

    try:
        runtime_client = get_runtime_client(region, credentials=runtime_credentials)
        runtime_response = runtime_client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            payload=json.dumps(
                _build_runtime_payload(agent, tenant_context, prompt, session_id=session_id)
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
        runtime_session_id = _coerce_optional_string(runtime_response.get("runtimeSessionId"))
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
        return _runtime_failure_response(
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
) -> dict[str, Any] | None:
    del webhook_id
    del response_stream

    invocation_id = invocation_id or str(uuid.uuid4())
    start_time = start_time or time.time()
    try:
        response = get_http_session().post(
            url.rstrip("/"),
            json=_build_runtime_payload(agent, tenant_context, prompt, session_id=session_id),
            timeout=AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS,
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
        return _runtime_failure_response(
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


def get_authorizer_map(event: dict[str, Any]) -> dict[str, str]:
    request_context = event.get("requestContext", {})
    authorizer = request_context.get("authorizer", {})
    if not isinstance(authorizer, dict):
        return {}
    if "lambda" in authorizer and isinstance(authorizer["lambda"], dict):
        return authorizer["lambda"]
    return authorizer


def is_invoke_contract_path(path: str, agent_name: str | None) -> bool:
    if not agent_name:
        return False
    return path.endswith(f"/agents/{agent_name}/invoke")


@tracer.capture_lambda_handler
@logger.inject_lambda_context(
    clear_state=True, log_event=True, correlation_id_path=correlation_paths.API_GATEWAY_REST
)
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    request_id = context.aws_request_id
    auth_map = get_authorizer_map(event)

    tenant_id = auth_map.get("tenantId") or "unknown"
    app_id = auth_map.get("appId") or "unknown"
    tier_raw = auth_map.get("tier") or "standard"
    try:
        tier = TenantTier(tier_raw.lower())
    except ValueError:
        tier = TenantTier.STANDARD

    tenant_context = TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=tier,
        sub=auth_map.get("sub") or "system",
    )

    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}

    return handle_invoke_request(
        event=event,
        request_id=request_id,
        tenant_context=tenant_context,
        path=path,
        path_params=path_params,
        response_stream=None,
        error_response=error_response,
        parse_body=lambda e: json.loads(e.get("body") or "{}"),
        coerce_optional_string=_coerce_optional_string,
        is_invoke_contract_path=is_invoke_contract_path,
        get_agent_record=get_agent_record,
        get_capability_client=get_capability_client,
        invoke_agent=lambda a, tc, p, s, w, r, rs: invoke_real_runtime(
            region=get_config()["runtime_region"],
            agent=a,
            tenant_context=tc,
            prompt=p,
            session_id=s,
            webhook_id=w,
            request_id=r,
            response_stream=rs,
            invocation_id=str(uuid.uuid4()),
            start_time=time.time(),
        ),
    )
