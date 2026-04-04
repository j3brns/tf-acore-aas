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
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from data_access import (
    ControlPlaneDynamoDB,
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
    constants,
    lock_manager,
    role_resolver,
    route_adapter,
    runtime_calls,
    runtime_dependencies,
    telemetry,
)
from src.bridge.constants import (
    AG_UI_SCOPE_NAME,
    AGENTS_TABLE,
    BFF_SESSION_KEEPALIVE_PATH,
    BFF_TOKEN_REFRESH_PATH,
    ENTRA_AUDIENCE,
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
    SESSIONS_TABLE,
    TENANTS_TABLE,
)

from .discovery_service import (
    get_agent_detail as discovery_get_agent_detail,
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

_ssm_client: Any | None = None
_sts_client: Any | None = None
_cloudwatch_client: Any | None = None


get_capability_client = runtime_dependencies.get_capability_client
get_ssm = runtime_dependencies.get_ssm
get_sts = runtime_dependencies.get_sts
get_cloudwatch = runtime_dependencies.get_cloudwatch
get_http_session = runtime_dependencies.get_http_session
get_config = runtime_dependencies.get_config
get_runtime_client = runtime_dependencies.get_runtime_client


def trigger_failover(current_region: str) -> str:
    return lock_manager.trigger_failover(
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


def _get_execution_role_arn_from_ssm(tenant_id: str) -> str | None:
    return _resolve_tenant_execution_role(get_ssm(), tenant_id=tenant_id)


def assume_tenant_role(tenant_id: str, role_arn: str) -> dict[str, Any]:
    return _assume_tenant_role(
        get_sts(),
        role_arn=role_arn,
        session_name=f"invoke-{tenant_id[:8]}",
    )


def get_platform_context() -> TenantContext:
    return runtime_dependencies.get_platform_context()


def get_tenant_record(tenant_context: TenantContext) -> dict[str, Any] | None:
    record = runtime_dependencies.get_tenant_record(tenant_context)
    if record is None:
        logger.exception("Failed to fetch tenant record")
    return record


def get_agent_record(agent_name: str, agent_version: str | None = None) -> AgentRecord | None:
    return discovery_resolve_agent_record(
        ControlPlaneDynamoDB(get_platform_context()),
        agents_table=AGENTS_TABLE,
        agent_name=agent_name,
        agent_version=agent_version,
    )


def get_job_status(
    path_params: dict[str, Any],
    request_id: str,
    tenant_context: TenantContext,
) -> dict[str, Any]:
    return discovery_get_job_status(
        tenant_context,
        path_params,
        request_id,
        jobs_table=JOBS_TABLE,
        job_results_bucket=JOB_RESULTS_BUCKET,
        job_result_url_expiry_seconds=JOB_RESULT_URL_EXPIRY_SECONDS,
        error_response=error_response,
    )


def get_webhook_registration(
    tenant_context: TenantContext, webhook_id: str
) -> dict[str, Any] | None:
    record = runtime_dependencies.get_webhook_registration(tenant_context, webhook_id)
    if record is None:
        logger.exception("Failed to fetch webhook registration")
    return record


def error_response(status_code: int, code: str, message: str, request_id: str) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "x-amzn-RequestId": request_id},
        "body": json.dumps({"error": {"code": code, "message": message, "requestId": request_id}}),
    }


def _coerce_optional_string(val: Any) -> str | None:
    return runtime_calls.coerce_optional_string(val)


def get_jitter() -> str:
    """Return a random 2-character hex jitter for hot-partition mitigation."""
    return runtime_calls.get_jitter()


def _validate_execution_role_arn(role_arn: str, expected_account_id: str) -> str:
    return runtime_calls.validate_execution_role_arn(
        role_arn, expected_account_id, IAM_ROLE_ARN_PATTERN
    )


def _build_runtime_payload(
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    return runtime_calls.build_runtime_payload(agent, tenant_context, prompt, session_id=session_id)


def _validate_runtime_arn(runtime_arn: str) -> re.Match[str]:
    return runtime_calls.validate_runtime_arn(runtime_arn, RUNTIME_ARN_PATTERN)


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
    return runtime_calls.runtime_failure_response(
        tenant_context,
        agent,
        invocation_id,
        start_time,
        invocation_mode,
        runtime_region,
        request_id,
        exc,
        session_id=session_id,
        emit_bedrock_throttle_metric=emit_bedrock_throttle_metric,
        log_invocation=log_invocation,
        error_response=error_response,
    )


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
    return runtime_calls.invoke_real_runtime(
        region,
        agent,
        tenant_context,
        prompt,
        session_id,
        webhook_id,
        request_id,
        response_stream,
        invocation_id,
        start_time,
        runtime_credentials,
        coerce_optional_string=_coerce_optional_string,
        validate_runtime_arn=_validate_runtime_arn,
        get_tenant_record=get_tenant_record,
        resolve_tenant_execution_role=lambda _ssm, tenant_id: _get_execution_role_arn_from_ssm(
            tenant_id
        ),
        get_ssm=get_ssm,
        validate_execution_role_arn=_validate_execution_role_arn,
        get_sts=get_sts,
        assume_tenant_role=lambda _sts, role_arn, session_name: assume_tenant_role(
            tenant_context.tenant_id, role_arn
        ),
        get_runtime_client=get_runtime_client,
        build_runtime_payload=_build_runtime_payload,
        log_invocation=log_invocation,
        runtime_failure_response=_runtime_failure_response,
        error_response=error_response,
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
    return runtime_calls.invoke_mock_runtime(
        url,
        agent,
        tenant_context,
        prompt,
        session_id,
        webhook_id,
        request_id,
        response_stream,
        invocation_id,
        start_time,
        get_http_session=get_http_session,
        build_runtime_payload=_build_runtime_payload,
        log_invocation=log_invocation,
        runtime_failure_response=_runtime_failure_response,
    )


_send_streaming_response = route_adapter.send_streaming_response
_mock_runtime_response_body = route_adapter.mock_runtime_response_body
_is_runtime_unavailable_error = route_adapter.is_runtime_unavailable_error


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
    get_http_session: Any | None = None,
    log_invocation: Any | None = None,
    runtime_failure_response: Any | None = None,
    error_response: Any | None = None,
) -> dict[str, Any] | None:
    return route_adapter.handle_streaming_invocation(
        url=url,
        payload=payload,
        agent=agent,
        tenant_context=tenant_context,
        invocation_id=invocation_id,
        start_time=start_time,
        response_stream=response_stream,
        request_id=request_id,
        session_id=session_id,
        get_http_session=get_http_session or globals()["get_http_session"],
        log_invocation=log_invocation or globals()["log_invocation"],
        runtime_failure_response=runtime_failure_response or _runtime_failure_response,
        error_response=error_response or globals()["error_response"],
    )


def invoke_agent(
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any | None,
) -> Any:
    return route_adapter.invoke_agent(
        agent=agent,
        tenant_context=tenant_context,
        prompt=prompt,
        session_id=session_id,
        webhook_id=webhook_id,
        request_id=request_id,
        response_stream=response_stream,
        get_config=get_config,
        coerce_optional_string=_coerce_optional_string,
        get_webhook_registration=get_webhook_registration,
        error_response=error_response,
        build_runtime_payload=_build_runtime_payload,
        get_http_session=get_http_session,
        invoke_mock_runtime=invoke_mock_runtime,
        handle_streaming_invocation=handle_streaming_invocation,
        log_invocation=log_invocation,
        invoke_real_runtime=invoke_real_runtime,
        runtime_failure_response=_runtime_failure_response,
        trigger_failover=trigger_failover,
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


_normalize_contract_path = route_adapter.normalize_contract_path
_is_job_contract_path = route_adapter.is_job_contract_path
_is_agent_detail_path = route_adapter.is_agent_detail_path
_is_agents_list_path = route_adapter.is_agents_list_path
_is_agent_bootstrap_path = route_adapter.is_agent_bootstrap_path


def _bootstrap_agent_session(
    *,
    agent_name: str,
    tenant_context: TenantContext,
    request_id: str,
) -> dict[str, Any]:
    return route_adapter.bootstrap_agent_session(
        agent_name=agent_name,
        tenant_context=tenant_context,
        request_id=request_id,
        get_agent_record=get_agent_record,
        get_platform_context=get_platform_context,
        coerce_optional_string=_coerce_optional_string,
        entra_audience=ENTRA_AUDIENCE,
        ag_ui_scope_name=AG_UI_SCOPE_NAME,
    )


@tracer.capture_lambda_handler
@logger.inject_lambda_context(
    clear_state=True, log_event=True, correlation_id_path=correlation_paths.API_GATEWAY_REST
)
def handler(
    event: dict[str, Any],
    context: LambdaContext,
    response_stream: Any | None = None,
) -> dict[str, Any] | None:
    request_id = context.aws_request_id
    auth_map = get_authorizer_map(event)

    tenant_id = auth_map.get("tenantId") or auth_map.get("tenantid") or "unknown"
    app_id = auth_map.get("appId") or auth_map.get("appid") or "unknown"
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
    http_method = str(event.get("httpMethod", "")).upper()
    discovery_capability_policy = get_capability_client().fetch_policy()
    invoke_capability_policy = None
    if (
        os.environ.get("APPCONFIG_APPLICATION_ID")
        and os.environ.get("APPCONFIG_ENVIRONMENT_ID")
        and os.environ.get("APPCONFIG_PROFILE_ID")
    ):
        invoke_capability_policy = discovery_capability_policy

    agent_name = _coerce_optional_string(path_params.get("agentName"))
    job_id = _coerce_optional_string(path_params.get("jobId"))

    if http_method == "GET" and _is_agents_list_path(path):
        result = discovery_list_agents(
            tenant_context,
            agents_table=AGENTS_TABLE,
            db_factory=ControlPlaneDynamoDB,
            capability_policy=discovery_capability_policy,
        )
    elif http_method == "GET" and _is_agent_detail_path(path, agent_name):
        result = discovery_get_agent_detail(
            path_params,
            request_id,
            agents_table=AGENTS_TABLE,
            db_factory=ControlPlaneDynamoDB,
            error_response=error_response,
            tenant_context=tenant_context,
            capability_policy=discovery_capability_policy,
        )
    elif http_method == "POST" and _is_agent_bootstrap_path(path, agent_name):
        result = _bootstrap_agent_session(
            agent_name=agent_name or "",
            tenant_context=tenant_context,
            request_id=request_id,
        )
    elif http_method == "GET" and _is_job_contract_path(path, job_id):
        result = get_job_status(path_params, request_id, tenant_context)
    else:
        result = handle_invoke_request(
            event=event,
            request_id=request_id,
            tenant_context=tenant_context,
            path=path,
            path_params=path_params,
            response_stream=response_stream,
            error_response=error_response,
            parse_body=lambda e: json.loads(e.get("body") or "{}"),
            coerce_optional_string=_coerce_optional_string,
            is_invoke_contract_path=is_invoke_contract_path,
            get_agent_record=get_agent_record,
            capability_policy=invoke_capability_policy,
            invoke_agent=invoke_agent,
        )

    if response_stream is not None and isinstance(result, dict):
        _send_streaming_response(
            response_stream,
            int(result.get("statusCode", 200)),
            str(result.get("body", "")).encode("utf-8"),
            dict(result.get("headers", {})),
        )
        return None

    return result
