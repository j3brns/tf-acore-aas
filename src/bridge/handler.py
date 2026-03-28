"""
bridge.handler — Agent invocation bridge Lambda.

Reads invocation_mode from agent registry, assumes tenant execution role,
and routes to AgentCore Runtime via sync, streaming, or async paths.

ADRs: ADR-003, ADR-005, ADR-009, ADR-010
"""

import json
import os
import re
import secrets
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
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
from data_access import TenantCapabilityClient, TenantScopedDynamoDB, TenantScopedS3
from data_access.models import (
    AgentRecord,
    AgentStatus,
    InvocationMode,
    InvocationRecord,
    InvocationStatus,
    JobRecord,
    JobStatus,
    TenantContext,
    TenantTier,
    is_invokable_agent_status,
    normalize_agent_status,
)

from .config_provider import ConfigProvider
from .discovery_service import (
    _agent_record_sort_key,
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
from .invocation_engine import handle_invoke_request
from .runtime_invoker import RuntimeInvoker
from .runtime_orchestrator import build_runtime_orchestrator

logger = Logger(service="bridge")
tracer = Tracer()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TENANTS_TABLE = os.environ.get("TENANTS_TABLE", "platform-tenants")
AGENTS_TABLE = os.environ.get("AGENTS_TABLE", "platform-agents")
INVOCATIONS_TABLE = os.environ.get("INVOCATIONS_TABLE", "platform-invocations")
JOBS_TABLE = os.environ.get("JOBS_TABLE", "platform-jobs")
OPS_LOCKS_TABLE = os.environ.get("OPS_LOCKS_TABLE", "platform-ops-locks")
JOB_RESULTS_BUCKET = os.environ.get("JOB_RESULTS_BUCKET")

RUNTIME_REGION_PARAM = os.environ.get("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")
MOCK_RUNTIME_URL_PARAM = os.environ.get(
    "MOCK_RUNTIME_URL_PARAM", "/platform/config/mock-runtime-url"
)
TENANT_EXECUTION_ROLE_PARAM_TEMPLATE = os.environ.get(
    "TENANT_EXECUTION_ROLE_PARAM_TEMPLATE", "/platform/tenants/{tenant_id}/execution-role-arn"
)
JOB_RESULT_URL_EXPIRY_SECONDS = int(os.environ.get("JOB_RESULT_URL_EXPIRY_SECONDS", "3600"))
AGENTCORE_RUNTIME_ENDPOINT_URL = os.environ.get("BEDROCK_AGENTCORE_DP_ENDPOINT")
AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS = int(
    os.environ.get("AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS", "5")
)
AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS = int(
    os.environ.get("AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS", "900")
)
IAM_ROLE_ARN_PATTERN = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):iam::(?P<account_id>\d{12}):role/(?P<role_name>[\w+=,.@\-_/]+)$"
)
RUNTIME_ARN_PATTERN = re.compile(
    r"^arn:(?P<partition>aws|aws-us-gov|aws-cn):bedrock-agentcore:(?P<region>[a-z0-9-]+):"
    r"(?P<account_id>\d{12}):runtime/(?P<runtime_id>[\w+=,.@\-_/]+)$"
)

# TTL constants from models
INVOCATION_TTL_SECONDS = 90 * 24 * 60 * 60
JOB_TTL_SECONDS = 7 * 24 * 60 * 60
VALID_WEBHOOK_EVENTS = {"job.completed", "job.failed"}

# ---------------------------------------------------------------------------
# Global clients/cache
# ---------------------------------------------------------------------------
_ssm_client = None
_sts_client = None
_dynamodb_resource = None
_cloudwatch_client = None
_capability_client = None
_http_session = None

# Cache for SSM parameters (60s TTL as per ARCHITECTURE.md)
_config_cache: dict[str, Any] = {}
_config_cache_expiry: float = 0
_config_provider: ConfigProvider | None = None


def _aws_region() -> str:
    return os.environ["AWS_REGION"]


def get_capability_client():
    global _capability_client
    if _capability_client is None:
        _capability_client = TenantCapabilityClient()
    return _capability_client


def get_ssm():
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm", region_name=_aws_region())
    return _ssm_client


def get_sts():
    global _sts_client
    if _sts_client is None:
        _sts_client = boto3.client("sts", region_name=_aws_region())
    return _sts_client


def get_dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb", region_name=_aws_region())
    return _dynamodb_resource


def get_cloudwatch():
    global _cloudwatch_client
    if _cloudwatch_client is None:
        _cloudwatch_client = boto3.client("cloudwatch", region_name=_aws_region())
    return _cloudwatch_client


def get_http_session():
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
    return _http_session


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
    if AGENTCORE_RUNTIME_ENDPOINT_URL:
        client_kwargs["endpoint_url"] = AGENTCORE_RUNTIME_ENDPOINT_URL
    return session.client(**client_kwargs)


def _fetch_ssm_config() -> dict[str, Any]:
    """Fetch Bridge runtime configuration from SSM."""
    try:
        ssm = get_ssm()
        names = [RUNTIME_REGION_PARAM, MOCK_RUNTIME_URL_PARAM]
        response = ssm.get_parameters(Names=names)

        params: dict[str, str] = {
            str(p.get("Name")): str(p.get("Value"))
            for p in response.get("Parameters", [])
            if p.get("Name") and p.get("Value")
        }

        _config_cache = {
            "runtime_region": params.get(RUNTIME_REGION_PARAM, "eu-west-1"),
            "mock_runtime_url": params.get(MOCK_RUNTIME_URL_PARAM),
        }
        return _config_cache
    except Exception:
        logger.exception("Failed to fetch config from SSM")
        raise


def _config_defaults() -> dict[str, Any]:
    return {"runtime_region": "eu-west-1", "mock_runtime_url": None}


def _get_config_provider() -> ConfigProvider:
    global _config_provider
    if _config_provider is None:
        _config_provider = ConfigProvider(
            fetcher=_fetch_ssm_config,
            fallback_factory=_config_defaults,
            ttl_seconds=60,
        )
    return _config_provider


def get_config(force_refresh: bool = False) -> dict[str, Any]:
    """Fetch and cache configuration from SSM."""
    global _config_cache, _config_cache_expiry
    provider = _get_config_provider()
    config = provider.get(force_refresh=force_refresh)
    _config_cache = dict(config)
    _config_cache_expiry = provider.expires_at
    return config


def acquire_lock(lock_name: str, identity: str, ttl_seconds: int = 300) -> str | None:
    """Acquire a distributed lock in DynamoDB.

    Returns lock_id (UUID) on success, None on failure.
    """
    lock_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    ttl = int(now.timestamp()) + ttl_seconds

    try:
        ddb = get_dynamodb()
        table = ddb.Table(OPS_LOCKS_TABLE)

        table.put_item(
            Item={
                "PK": f"LOCK#{lock_name}",
                "SK": "METADATA",
                "lock_id": lock_id,
                "acquired_by": identity,
                "acquired_at": now.isoformat(),
                "ttl": ttl,
            },
            ConditionExpression="attribute_not_exists(PK) OR #ttl < :now",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={":now": int(time.time())},
        )
        return lock_id
    except Exception:
        # ConditionalCheckFailedException or any other error
        return None


def release_lock(lock_name: str, lock_id: str) -> bool:
    """Release a distributed lock if lock_id matches."""
    try:
        ddb = get_dynamodb()
        table = ddb.Table(OPS_LOCKS_TABLE)

        table.delete_item(
            Key={"PK": f"LOCK#{lock_name}", "SK": "METADATA"},
            ConditionExpression="lock_id = :lock_id",
            ExpressionAttributeValues={":lock_id": lock_id},
        )
        return True
    except Exception:
        logger.warning(
            "Failed to release ops lock; will expire via TTL",
            extra={"lock_name": lock_name},
        )
        return False


def trigger_failover(current_region: str) -> str:
    """Failover from eu-west-1 to eu-central-1 (or vice versa).

    Uses distributed lock to ensure only one Lambda instance performs the update.
    Returns the new active region.
    """
    new_region = "eu-central-1" if current_region == "eu-west-1" else "eu-west-1"
    lock_name = "runtime-region-failover"
    identity = f"bridge-lambda-{os.environ.get('AWS_LAMBDA_LOG_STREAM_NAME', 'local')}"

    lock_id = acquire_lock(lock_name, identity)
    if not lock_id:
        logger.info("Failover in progress by another instance, skipping update")
        # Wait a bit and re-fetch config
        time.sleep(2)
        config = get_config(force_refresh=True)
        return config["runtime_region"]

    try:
        # Re-fetch config to ensure we still need to failover
        ssm = get_ssm()
        param_response = ssm.get_parameter(Name=RUNTIME_REGION_PARAM)
        param = param_response.get("Parameter", {})
        current_ssm_region = str(param.get("Value", current_region))

        if current_ssm_region != current_region:
            logger.info(
                "Region already changed by another instance",
                extra={"ssm_region": current_ssm_region},
            )
            return current_ssm_region

        logger.warning(
            "Triggering region failover", extra={"from": current_region, "to": new_region}
        )
        ssm.put_parameter(
            Name=RUNTIME_REGION_PARAM, Value=new_region, Type="String", Overwrite=True
        )

        # Clear local cache
        global _config_cache_expiry
        _config_cache_expiry = 0

        return new_region
    except Exception:
        logger.exception("Failed to trigger failover")
        return current_region
    finally:
        release_lock(lock_name, lock_id)


def get_tenant_record(tenant_context: TenantContext) -> dict[str, Any] | None:
    """Fetch tenant metadata from the registry."""
    try:
        db = TenantScopedDynamoDB(tenant_context)
        return db.get_item(
            TENANTS_TABLE, {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": "METADATA"}
        )
    except Exception:
        logger.exception("Failed to fetch tenant record")
        return None


def get_agent_record(agent_name: str, version: str | None = None) -> AgentRecord | None:
    """Fetch agent metadata from the registry."""
    try:
        ddb = get_dynamodb()
        table = ddb.Table(AGENTS_TABLE)

        if not version:
            # Query for latest versions and find the highest semver that is tenant-invokable.
            response = table.query(
                KeyConditionExpression=Key("PK").eq(f"AGENT#{agent_name}"),
                ScanIndexForward=False,  # Highest version SK first
            )
            items = response.get("Items", [])
            if not items:
                return None

            invokable_items = [
                i
                for i in items
                if is_invokable_agent_status(_coerce_optional_string(i.get("status")))
            ]
            item = max(invokable_items, key=_agent_record_sort_key, default=None)
        else:
            response = table.get_item(Key={"PK": f"AGENT#{agent_name}", "SK": f"VERSION#{version}"})
            item = response.get("Item")
            item_status = _coerce_optional_string(item.get("status")) if item else None
            if item and not is_invokable_agent_status(item_status):
                logger.warning(
                    "Requested agent version is not invokable",
                    extra={
                        "agent_name": agent_name,
                        "version": version,
                        "status": normalize_agent_status(
                            item_status,
                            default=AgentStatus.PROMOTED,
                        ).value,
                    },
                )
                return None

        if not item:
            return None

        return AgentRecord(
            agent_name=str(item["agent_name"]),
            version=str(item["version"]),
            owner_team=str(item["owner_team"]),
            tier_minimum=TenantTier(str(item["tier_minimum"])),
            layer_hash=str(item["layer_hash"]),
            layer_s3_key=str(item["layer_s3_key"]),
            script_s3_key=str(item["script_s3_key"]),
            deployed_at=str(item["deployed_at"]),
            invocation_mode=InvocationMode(str(item["invocation_mode"])),
            streaming_enabled=bool(item.get("streaming_enabled", False)),
            status=normalize_agent_status(
                _coerce_optional_string(item.get("status")),
                default=AgentStatus.PROMOTED,
            ),
            approved_by=_coerce_optional_string(item.get("approved_by")),
            approved_at=_coerce_optional_string(item.get("approved_at")),
            release_notes=_coerce_optional_string(item.get("release_notes")),
            runtime_arn=str(item["runtime_arn"]) if item.get("runtime_arn") else None,
            estimated_duration_seconds=int(item["estimated_duration_seconds"])  # type: ignore
            if item.get("estimated_duration_seconds")
            else None,
            commit_sha=_coerce_optional_string(item.get("commit_sha")),
            pipeline_url=_coerce_optional_string(item.get("pipeline_url")),
            job_id=_coerce_optional_string(item.get("job_id")),
            evaluation_score=(
                float(item["evaluation_score"])  # type: ignore
                if item.get("evaluation_score") is not None
                else None
            ),
            evaluation_report_url=_coerce_optional_string(item.get("evaluation_report_url")),
            rolled_back_by=_coerce_optional_string(item.get("rolled_back_by")),
            rolled_back_at=_coerce_optional_string(item.get("rolled_back_at")),
        )
    except Exception:
        logger.exception("Failed to fetch agent record", extra={"agent_name": agent_name})
        return None


def _send_streaming_response(
    response_stream: Any,
    status_code: int,
    body_bytes: bytes,
    headers: dict[str, str] | None = None,
) -> None:
    """Send a buffered response via response_stream (metadata preamble + body)."""
    effective_headers = headers or {"Content-Type": "application/json"}
    preamble = {
        "statusCode": status_code,
        "headers": effective_headers,
    }
    # For REST API response streaming, the preamble is a JSON object followed by a null byte.
    response_stream.write(json.dumps(preamble).encode("utf-8") + b"\0")
    response_stream.write(body_bytes)


def error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a standard error response as per openapi.yaml."""
    response_headers = {"Content-Type": "application/json"}
    if headers:
        response_headers.update(headers)
    return {
        "statusCode": status_code,
        "headers": response_headers,
        "body": json.dumps({"error": {"code": code, "message": message, "requestId": request_id}}),
    }


def _tenant_execution_role_param_name(tenant_id: str) -> str:
    return TENANT_EXECUTION_ROLE_PARAM_TEMPLATE.format(tenant_id=tenant_id)


def _get_execution_role_arn_from_ssm(tenant_id: str) -> str | None:
    parameter_name = _tenant_execution_role_param_name(tenant_id)
    try:
        ssm = get_ssm()
        response = ssm.get_parameter(Name=parameter_name)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code == "ParameterNotFound":
            logger.warning(
                "Tenant execution role ARN parameter not found",
                extra={"tenant_id": tenant_id, "parameter_name": parameter_name},
            )
            return None
        logger.exception(
            "Failed to fetch tenant execution role ARN from SSM",
            extra={"tenant_id": tenant_id, "parameter_name": parameter_name},
        )
        raise

    parameter = response.get("Parameter", {})
    role_arn = _coerce_optional_string(parameter.get("Value"))
    if role_arn is None:
        logger.warning(
            "Tenant execution role ARN parameter is empty",
            extra={"tenant_id": tenant_id, "parameter_name": parameter_name},
        )
    return role_arn


def _validate_execution_role_arn(role_arn: str, expected_account_id: str) -> str:
    match = IAM_ROLE_ARN_PATTERN.fullmatch(role_arn)
    if not match:
        raise ValueError("Tenant execution role ARN is malformed")

    if match.group("account_id") != expected_account_id:
        raise ValueError("Tenant execution role ARN account mismatch")

    return role_arn


def resolve_tenant_execution_role_arn(
    tenant: dict[str, Any], *, tenant_id: str, account_id: str
) -> str | None:
    role_arn = _coerce_optional_string(
        tenant.get("execution_role_arn") or tenant.get("executionRoleArn")
    )
    source = "tenant-record"
    if role_arn is None:
        role_arn = _get_execution_role_arn_from_ssm(tenant_id)
        source = "ssm"
    if role_arn is None:
        return None

    validated = _validate_execution_role_arn(role_arn, expected_account_id=account_id)
    logger.info(
        "Resolved tenant execution role ARN",
        extra={"tenant_id": tenant_id, "account_id": account_id, "source": source},
    )
    return validated


def assume_tenant_role(tenant_id: str, role_arn: str) -> dict[str, Any] | None:
    """Assume the tenant's execution role via STS.

    Returns temporary credentials, or None if in local/mock mode.
    """
    if os.environ.get("MOCK_RUNTIME") == "true":
        return None

    try:
        sts = get_sts()
        response = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=f"bridge-{tenant_id}-{int(time.time())}",
            DurationSeconds=3600,
        )
        return dict(response["Credentials"])
    except Exception:
        logger.exception("Failed to assume tenant role", extra={"role_arn": role_arn})
        raise


def _validate_runtime_arn(runtime_arn: str) -> re.Match[str]:
    match = RUNTIME_ARN_PATTERN.fullmatch(runtime_arn)
    if not match:
        raise ValueError("Agent runtime ARN is malformed")
    return match


def _runtime_arn_for_region(runtime_arn: str, active_region: str) -> tuple[str, str]:
    match = _validate_runtime_arn(runtime_arn)
    runtime_id = match.group("runtime_id")
    account_id = match.group("account_id")
    partition = match.group("partition")
    active_runtime_arn = (
        f"arn:{partition}:bedrock-agentcore:{active_region}:{account_id}:runtime/{runtime_id}"
    )
    return active_runtime_arn, account_id


def _runtime_accept_header(mode: InvocationMode) -> str:
    if mode == InvocationMode.STREAMING:
        return "text/event-stream"
    return "application/json"


def _build_runtime_payload(
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
) -> bytes:
    payload: dict[str, Any] = {
        "prompt": prompt,
        "input": prompt,
        "mode": str(agent.invocation_mode),
        "appid": tenant_context.app_id,
        "tenantId": tenant_context.tenant_id,
        "agentName": agent.agent_name,
        "agentVersion": agent.version,
    }
    if session_id:
        payload["sessionId"] = session_id
    return json.dumps(payload).encode("utf-8")


def _close_runtime_body(body: Any) -> None:
    try:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    except Exception:
        logger.debug("Failed to close runtime response body")


def _iter_runtime_lines(body: Any) -> Any:
    if hasattr(body, "iter_lines"):
        return body.iter_lines()
    data = b""
    if hasattr(body, "read"):
        data = body.read()
    elif isinstance(body, (bytes, bytearray)):
        data = bytes(body)
    if isinstance(data, bytes):
        return data.splitlines()
    return []


def _read_runtime_body_text(body: Any) -> str:
    try:
        if hasattr(body, "read"):
            payload = body.read()
        elif isinstance(body, (bytes, bytearray)):
            payload = bytes(body)
        elif body is None:
            payload = b""
        else:
            payload = str(body).encode("utf-8")
        return payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
    finally:
        _close_runtime_body(body)


def _collect_sse_text(body: Any, session_id: str | None) -> tuple[str, str | None]:
    full_text = ""
    effective_session_id = session_id
    try:
        for line in _iter_runtime_lines(body):
            if not line:
                continue
            decoded_line = line.decode("utf-8") if isinstance(line, bytes) else str(line)
            if not decoded_line.startswith("data: "):
                continue
            data = decoded_line[6:]
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("type") == "text":
                full_text += str(chunk.get("content", ""))
            elif chunk.get("type") == "session":
                effective_session_id = (
                    _coerce_optional_string(chunk.get("sessionId")) or effective_session_id
                )
    finally:
        _close_runtime_body(body)
    return full_text, effective_session_id


def _extract_runtime_output(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("output", "echo", "result", "message"):
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False)
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False)


def _is_runtime_unavailable_error(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response.status_code == 503:
        return True
    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError)):
        return True
    if isinstance(exc, ClientError):
        http_status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        return http_status == 503 or error_code == "ServiceUnavailableException"
    return False


def _map_runtime_exception(
    exc: Exception,
) -> tuple[int, str, str, InvocationStatus, dict[str, str] | None]:
    if isinstance(exc, (ReadTimeoutError, requests.exceptions.ReadTimeout)):
        return (
            504,
            "GATEWAY_TIMEOUT",
            "Agent runtime timed out",
            InvocationStatus.TIMEOUT,
            None,
        )

    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        error_code = str(error.get("Code", ""))
        message = str(error.get("Message", "")) or "Agent runtime request failed"
        http_status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))

        if (
            error_code in {"ThrottlingException", "ServiceQuotaExceededException"}
            or http_status == 429
        ):
            return (
                429,
                "THROTTLED",
                "Agent runtime throttled the request",
                InvocationStatus.THROTTLED,
                {"Retry-After": "1"},
            )
        if error_code in {"AccessDeniedException", "UnauthorizedException"} or http_status in {
            401,
            403,
        }:
            return (
                502,
                "BAD_GATEWAY",
                "Agent runtime authentication failed",
                InvocationStatus.ERROR,
                None,
            )
        if _is_runtime_unavailable_error(exc):
            return (
                503,
                "SERVICE_UNAVAILABLE",
                "Agent runtime is unavailable",
                InvocationStatus.ERROR,
                None,
            )
        return 502, "BAD_GATEWAY", message, InvocationStatus.ERROR, None

    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError)):
        return (
            503,
            "SERVICE_UNAVAILABLE",
            "Agent runtime is unavailable",
            InvocationStatus.ERROR,
            None,
        )

    return (
        502,
        "BAD_GATEWAY",
        "Failed to communicate with agent runtime",
        InvocationStatus.ERROR,
        None,
    )


def _runtime_failure_response(
    tenant_context: TenantContext,
    agent: AgentRecord,
    invocation_id: str,
    start_time: float,
    mode: InvocationMode,
    runtime_region: str,
    request_id: str,
    exc: Exception | None = None,
    *,
    status_code: int | None = None,
    code: str | None = None,
    message: str | None = None,
    invocation_status: InvocationStatus | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    if exc is not None:
        status_code, code, message, invocation_status, headers = _map_runtime_exception(exc)
    else:
        headers = None

    resolved_status_code = status_code or 500
    resolved_code = code or "INTERNAL_ERROR"
    resolved_message = message or "Agent runtime invocation failed"
    resolved_invocation_status = invocation_status or InvocationStatus.ERROR

    if resolved_invocation_status == InvocationStatus.THROTTLED:
        emit_bedrock_throttle_metric(
            tenant_context=tenant_context,
            agent=agent,
            runtime_region=runtime_region,
        )

    latency_ms = int((time.time() - start_time) * 1000)
    log_invocation(
        tenant_context,
        agent,
        invocation_id,
        resolved_invocation_status,
        latency_ms,
        mode,
        session_id=session_id,
        error_code=resolved_code,
        runtime_region=runtime_region,
    )
    return error_response(
        resolved_status_code,
        resolved_code,
        resolved_message,
        request_id,
        headers=headers,
    )


def get_jitter() -> str:
    """Generate a 2-character random hex suffix for hot-partition mitigation."""
    return secrets.token_hex(1)


def _handler_core(
    event: dict[str, Any], context: LambdaContext, response_stream: Any = None
) -> Any:
    """Core logic for Bridge Lambda."""
    request_id = context.aws_request_id

    # 1. Parse Authorizer Context
    raw_authorizer = event.get("requestContext", {}).get("authorizer", {})
    if isinstance(raw_authorizer, dict) and isinstance(raw_authorizer.get("lambda"), dict):
        auth_context = raw_authorizer["lambda"]
    else:
        auth_context = raw_authorizer if isinstance(raw_authorizer, dict) else {}

    tenant_id = auth_context.get("tenantid") or auth_context.get("tenantId")
    app_id = auth_context.get("appid") or auth_context.get("appId")
    tier_str = str(auth_context.get("tier", "basic")).lower()
    sub = auth_context.get("sub", "unknown")

    if not tenant_id or not app_id:
        logger.error("Missing tenant context in authorizer")
        return error_response(401, "UNAUTHENTICATED", "Missing tenant context", request_id)

    try:
        tenant_tier = TenantTier(tier_str)
    except ValueError:
        tenant_tier = TenantTier.BASIC

    tenant_context = TenantContext(tenant_id=tenant_id, app_id=app_id, tier=tenant_tier, sub=sub)

    # Inject context into logs
    logger.append_keys(tenant_id=tenant_id, app_id=app_id)

    method = _http_method(event)
    path = _request_path(event)
    path_params = event.get("pathParameters", {})
    if not isinstance(path_params, dict):
        path_params = {}

    # 2. Route non-invocation APIs implemented in TASK-048.
    job_id = _coerce_optional_string(path_params.get("jobId"))
    if method == "GET" and job_id:
        if path and not _is_jobs_contract_path(path, job_id):
            return error_response(404, "NOT_FOUND", "Route not found", request_id)
        return discovery_get_job_status(
            tenant_context,
            path_params,
            request_id,
            jobs_table=JOBS_TABLE,
            job_results_bucket=JOB_RESULTS_BUCKET,
            job_result_url_expiry_seconds=JOB_RESULT_URL_EXPIRY_SECONDS,
            error_response=error_response,
            db_factory=TenantScopedDynamoDB,
        )
    if method == "GET" and path.endswith("/v1/agents"):
        return discovery_list_agents(
            tenant_context,
            agents_table=AGENTS_TABLE,
            db_factory=TenantScopedDynamoDB,
        )
    if (
        method == "GET"
        and _coerce_optional_string(path_params.get("agentName"))
        and not path.endswith("/invoke")
    ):
        return discovery_get_agent_detail(
            path_params,
            request_id,
            agents_table=AGENTS_TABLE,
            get_dynamodb=get_dynamodb,
            error_response=error_response,
        )

    # 3. Contracted invoke route: POST /v1/agents/{agentName}/invoke.
    if method != "POST":
        return error_response(404, "NOT_FOUND", "Route not found", request_id)

    return handle_invoke_request(
        event=event,
        request_id=request_id,
        tenant_context=tenant_context,
        path=path,
        path_params=path_params,
        response_stream=response_stream,
        error_response=error_response,
        parse_body=_parse_body,
        coerce_optional_string=_coerce_optional_string,
        is_invoke_contract_path=_is_invoke_contract_path,
        get_agent_record=get_agent_record,
        get_capability_client=get_capability_client,
        invoke_agent=invoke_agent,
    )


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext, response_stream: Any = None) -> Any:
    """Bridge Lambda entry point."""
    result = _handler_core(event, context, response_stream)

    # If response_stream is present and we have a standard response dict, stream it.
    if response_stream and isinstance(result, dict) and "statusCode" in result:
        body = result.get("body", "")
        body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        _send_streaming_response(
            response_stream,
            status_code=result["statusCode"],
            headers=result.get("headers"),
            body_bytes=body_bytes,
        )
        return None

    return result


def _http_method(event: dict[str, Any]) -> str:
    method = event.get("httpMethod")
    if not method:
        method = event.get("requestContext", {}).get("http", {}).get("method")
    if not method:
        return "POST"
    return str(method).upper()


def _request_path(event: dict[str, Any]) -> str:
    path = event.get("path")
    if not path:
        path = event.get("rawPath")
    if not path:
        path = event.get("requestContext", {}).get("http", {}).get("path")
    if not path:
        return ""
    return str(path)


def _is_invoke_contract_path(path: str, agent_name: str | None) -> bool:
    normalized = str(path).rstrip("/")
    if not normalized.endswith("/invoke"):
        return False

    # Accept optional stage prefixes and validate the right-most contract path.
    segments = [segment for segment in normalized.split("/") if segment]
    if len(segments) < 4:
        return False
    if segments[-4] != "v1" or segments[-3] != "agents" or segments[-1] != "invoke":
        return False

    route_agent_name = segments[-2].strip()
    if not route_agent_name:
        return False
    return agent_name is None or route_agent_name == agent_name


def _is_jobs_contract_path(path: str, job_id: str | None) -> bool:
    normalized = str(path).rstrip("/")
    segments = [segment for segment in normalized.split("/") if segment]
    if len(segments) < 3:
        return False
    if segments[-3] != "v1" or segments[-2] != "jobs":
        return False

    route_job_id = segments[-1].strip()
    if not route_job_id:
        return False
    return job_id is None or route_job_id == job_id


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw_body = event.get("body")
    if raw_body in (None, ""):
        return {}
    if not isinstance(raw_body, str):
        raise ValueError("Request body must be a JSON string")

    body = json.loads(raw_body)
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    return body


def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _webhook_key(tenant_id: str, webhook_id: str) -> dict[str, str]:
    return {"PK": f"TENANT#{tenant_id}", "SK": f"WEBHOOK#{webhook_id}"}


def get_webhook_registration(
    tenant_context: TenantContext, webhook_id: str
) -> dict[str, Any] | None:
    key = _webhook_key(tenant_context.tenant_id, webhook_id)
    db = TenantScopedDynamoDB(tenant_context)
    record = db.get_item(TENANTS_TABLE, key)
    if record is None:
        return None
    if str(record.get("tenant_id", "")) != tenant_context.tenant_id:
        return None
    if _coerce_optional_string(record.get("callback_url")) is None:
        return None
    return record


def _runtime_invoker() -> RuntimeInvoker:
    return build_runtime_orchestrator(
        get_config=get_config,
        invoke_mock_runtime=invoke_mock_runtime,
        invoke_real_runtime=invoke_real_runtime,
        is_runtime_unavailable_error=_is_runtime_unavailable_error,
        trigger_failover=trigger_failover,
        runtime_failure_response=_runtime_failure_response,
        log_warning=logger.warning,
        log_exception=logger.exception,
    )


def invoke_agent(
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any,
) -> Any:
    """Invoke the agent with runtime selection and failover policy."""
    return _runtime_invoker().invoke(
        agent=agent,
        tenant_context=tenant_context,
        prompt=prompt,
        session_id=session_id,
        webhook_id=webhook_id,
        request_id=request_id,
        response_stream=response_stream,
    )


def invoke_real_runtime(
    region: str,
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any,
    invocation_id: str,
    start_time: float,
) -> Any:
    """Invoke the real AgentCore Runtime."""
    # 1. Get tenant record to find account_id
    tenant = get_tenant_record(tenant_context)
    if not tenant:
        return _runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            region,
            request_id,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Tenant record not found",
            session_id=session_id,
        )

    account_id = tenant.get("account_id") or tenant.get("accountId")
    if not account_id:
        return _runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            region,
            request_id,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Tenant account_id not configured",
            session_id=session_id,
        )

    account_id_str = str(account_id)
    try:
        execution_role_arn = resolve_tenant_execution_role_arn(
            tenant,
            tenant_id=tenant_context.tenant_id,
            account_id=account_id_str,
        )
    except ValueError as exc:
        logger.warning(
            "Tenant execution role ARN validation failed",
            extra={"tenant_id": tenant_context.tenant_id, "account_id": account_id_str},
        )
        return _runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            region,
            request_id,
            status_code=500,
            code="INTERNAL_ERROR",
            message=str(exc),
            session_id=session_id,
        )
    except Exception:
        return _runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            region,
            request_id,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Failed to resolve tenant execution role ARN",
            session_id=session_id,
        )

    if not execution_role_arn:
        return _runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            region,
            request_id,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Tenant execution role ARN not configured",
            session_id=session_id,
        )

    # 2. Assume tenant role
    try:
        runtime_credentials = assume_tenant_role(tenant_context.tenant_id, execution_role_arn)
        logger.info(
            "Assumed tenant role",
            extra={
                "account_id": account_id_str,
                "region": region,
                "role_arn": execution_role_arn,
            },
        )
    except Exception:
        return _runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            region,
            request_id,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Failed to assume tenant role",
            session_id=session_id,
        )

    runtime_arn_raw = _coerce_optional_string(agent.runtime_arn)
    if runtime_arn_raw is None:
        return _runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            region,
            request_id,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Agent runtime ARN not configured",
            session_id=session_id,
        )

    try:
        active_runtime_arn, runtime_account_id = _runtime_arn_for_region(runtime_arn_raw, region)
    except ValueError as exc:
        return _runtime_failure_response(
            tenant_context,
            agent,
            invocation_id,
            start_time,
            agent.invocation_mode,
            region,
            request_id,
            status_code=500,
            code="INTERNAL_ERROR",
            message=str(exc),
            session_id=session_id,
        )

    payload = _build_runtime_payload(agent, tenant_context, prompt, session_id)
    runtime_client = get_runtime_client(region, credentials=runtime_credentials)
    invoke_kwargs: dict[str, Any] = {
        "agentRuntimeArn": active_runtime_arn,
        "contentType": "application/json",
        "accept": _runtime_accept_header(agent.invocation_mode),
        "accountId": runtime_account_id,
        "traceId": request_id,
        "payload": payload,
    }
    if tenant_context.sub:
        invoke_kwargs["runtimeUserId"] = tenant_context.sub
    if session_id:
        invoke_kwargs["runtimeSessionId"] = session_id

    runtime_response = runtime_client.invoke_agent_runtime(**invoke_kwargs)
    runtime_body = runtime_response.get("response")
    runtime_content_type = str(runtime_response.get("contentType", "application/json")).lower()
    runtime_session_id = (
        _coerce_optional_string(runtime_response.get("runtimeSessionId")) or session_id
    )

    # Extract usage if available
    usage = runtime_response.get("usage", {})
    input_tokens = int(usage.get("inputTokens", 0))
    output_tokens = int(usage.get("outputTokens", 0))

    if agent.invocation_mode == InvocationMode.STREAMING:
        if not response_stream:
            _close_runtime_body(runtime_body)
            return _runtime_failure_response(
                tenant_context,
                agent,
                invocation_id,
                start_time,
                agent.invocation_mode,
                region,
                request_id,
                status_code=500,
                code="INTERNAL_ERROR",
                message="Response streaming not enabled for this Lambda",
                session_id=runtime_session_id,
            )

        # Send preamble for streaming (ADR-003 supported REST API streaming)
        preamble = {
            "statusCode": 200,
            "headers": {"Content-Type": "text/event-stream"},
        }
        response_stream.write(json.dumps(preamble).encode("utf-8") + b"\0")

        try:
            for line in _iter_runtime_lines(runtime_body):
                if line:
                    response_stream.write(line + b"\n\n")
        finally:
            _close_runtime_body(runtime_body)

        latency_ms = int((time.time() - start_time) * 1000)
        log_invocation(
            tenant_context,
            agent,
            invocation_id,
            InvocationStatus.SUCCESS,
            latency_ms,
            InvocationMode.STREAMING,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id=runtime_session_id,
            runtime_region=region,
        )
        return None

    if runtime_content_type.startswith("text/event-stream"):
        output_text, runtime_session_id = _collect_sse_text(runtime_body, runtime_session_id)
        runtime_payload: Any = {"output": output_text}
    else:
        body_text = _read_runtime_body_text(runtime_body)
        runtime_payload = json.loads(body_text or "{}")

    if agent.invocation_mode == InvocationMode.ASYNC:
        job_id = str(uuid.uuid4())
        now_iso = datetime.now(UTC).isoformat()
        now_ts = int(time.time())
        webhook_url: str | None = None

        if webhook_id:
            registration = get_webhook_registration(tenant_context, webhook_id)
            if registration is None:
                return _runtime_failure_response(
                    tenant_context,
                    agent,
                    invocation_id,
                    start_time,
                    agent.invocation_mode,
                    region,
                    request_id,
                    status_code=404,
                    code="NOT_FOUND",
                    message=f"Webhook '{webhook_id}' not found",
                    session_id=runtime_session_id,
                )
            webhook_url = _coerce_optional_string(registration.get("callback_url"))
            if webhook_url is None:
                return _runtime_failure_response(
                    tenant_context,
                    agent,
                    invocation_id,
                    start_time,
                    agent.invocation_mode,
                    region,
                    request_id,
                    status_code=500,
                    code="INTERNAL_ERROR",
                    message="Webhook registration missing callback URL",
                    session_id=runtime_session_id,
                )

        job_record = JobRecord(
            job_id=job_id,
            tenant_id=tenant_context.tenant_id,
            app_id=tenant_context.app_id,
            agent_name=agent.agent_name,
            status=JobStatus.PENDING,
            created_at=now_iso,
            ttl=now_ts + JOB_TTL_SECONDS,
            webhook_id=webhook_id,
            webhook_url=webhook_url,
        )
        log_job(tenant_context, job_record)

        latency_ms = int((time.time() - start_time) * 1000)
        log_invocation(
            tenant_context,
            agent,
            invocation_id,
            InvocationStatus.SUCCESS,
            latency_ms,
            InvocationMode.ASYNC,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            job_id=job_id,
            session_id=runtime_session_id or session_id or "async-session",
            runtime_region=region,
        )

        return {
            "statusCode": 202,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "jobId": job_id,
                    "status": "accepted",
                    "mode": "async",
                    "pollUrl": f"/v1/jobs/{job_id}",
                    "webhookDelivery": "registered" if webhook_url else "not_registered",
                }
            ),
        }

    latency_ms = int((time.time() - start_time) * 1000)
    output_text = _extract_runtime_output(runtime_payload)
    log_invocation(
        tenant_context,
        agent,
        invocation_id,
        InvocationStatus.SUCCESS,
        latency_ms,
        InvocationMode.SYNC,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        session_id=runtime_session_id or "unknown-session",
        runtime_region=region,
    )
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "invocationId": invocation_id,
                "agentName": agent.agent_name,
                "agentVersion": agent.version,
                "mode": InvocationMode.SYNC,
                "status": InvocationStatus.SUCCESS,
                "output": output_text,
                "sessionId": runtime_session_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "usage": {
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "latencyMs": latency_ms,
                },
            }
        ),
    }


def invoke_mock_runtime(
    url: str,
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any,
    invocation_id: str,
    start_time: float,
) -> Any:
    """Invoke the mock runtime via HTTP."""
    headers = {
        "x-tenant-id": tenant_context.tenant_id,
        "x-app-id": tenant_context.app_id,
        "x-tier": tenant_context.tier,
        "x-invocation-id": invocation_id,
        "x-agent-name": agent.agent_name,
        "Content-Type": "application/json",
    }
    if session_id:
        headers["x-session-id"] = session_id

    payload = {
        "input": prompt,
        "sessionId": session_id,
        "agentName": agent.agent_name,
        "agentVersion": agent.version,
    }

    if agent.invocation_mode == InvocationMode.STREAMING:
        # Handle streaming mode
        return handle_streaming_invocation(
            url,
            headers,
            payload,
            agent,
            tenant_context,
            invocation_id,
            start_time,
            response_stream,
            request_id,
            session_id,
        )
    elif agent.invocation_mode == InvocationMode.ASYNC:
        # Handle async mode
        return handle_async_invocation(
            url,
            headers,
            payload,
            agent,
            tenant_context,
            invocation_id,
            start_time,
            webhook_id,
            request_id,
            session_id,
        )
    else:
        # Default to sync mode
        return handle_sync_invocation(
            url, headers, payload, agent, tenant_context, invocation_id, start_time, session_id
        )


def handle_sync_invocation(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    agent: AgentRecord,
    tenant_context: TenantContext,
    invocation_id: str,
    start_time: float,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Handle synchronous invocation."""
    response = get_http_session().post(
        f"{url}/invocations", headers=headers, json=payload, timeout=900
    )
    response.raise_for_status()

    # Mock runtime returns SSE, collect into full text
    full_text = ""
    effective_session_id = session_id or "mock-session-id"

    for line in response.iter_lines():
        if line:
            decoded_line = line.decode("utf-8")
            if decoded_line.startswith("data: "):
                data = decoded_line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    if chunk.get("type") == "text":
                        full_text += chunk.get("content", "")
                    elif chunk.get("type") == "session":
                        effective_session_id = chunk.get("sessionId", effective_session_id)
                except json.JSONDecodeError:
                    pass

    latency_ms = int((time.time() - start_time) * 1000)

    # Log invocation
    log_invocation(
        tenant_context,
        agent,
        invocation_id,
        InvocationStatus.SUCCESS,
        latency_ms,
        InvocationMode.SYNC,
        input_tokens=0,
        output_tokens=0,
        session_id=effective_session_id,
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "invocationId": invocation_id,
                "agentName": agent.agent_name,
                "agentVersion": agent.version,
                "mode": InvocationMode.SYNC,
                "status": InvocationStatus.SUCCESS,
                "output": full_text,
                "sessionId": effective_session_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "usage": {"inputTokens": 0, "outputTokens": 0, "latencyMs": latency_ms},
            }
        ),
    }


def handle_streaming_invocation(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    agent: AgentRecord,
    tenant_context: TenantContext,
    invocation_id: str,
    start_time: float,
    response_stream: Any,
    request_id: str,
    session_id: str | None = None,
) -> Any:
    """Handle streaming invocation using Lambda Response Streaming."""
    if not response_stream:
        logger.error("Streaming requested but response_stream not available")
        return error_response(
            500, "INTERNAL_ERROR", "Response streaming not enabled for this Lambda", request_id
        )

    effective_session_id = session_id or "mock-session-id"

    # Send preamble for streaming
    preamble = {
        "statusCode": 200,
        "headers": {"Content-Type": "text/event-stream"},
    }
    response_stream.write(json.dumps(preamble).encode("utf-8") + b"\0")

    with get_http_session().post(
        f"{url}/invocations", headers=headers, json=payload, stream=True, timeout=900
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line:
                response_stream.write(line + b"\n\n")

    latency_ms = int((time.time() - start_time) * 1000)

    # Log invocation (after stream closes)
    log_invocation(
        tenant_context,
        agent,
        invocation_id,
        InvocationStatus.SUCCESS,
        latency_ms,
        InvocationMode.STREAMING,
        input_tokens=0,
        output_tokens=0,
        session_id=effective_session_id,
    )
    return None


def handle_async_invocation(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    agent: AgentRecord,
    tenant_context: TenantContext,
    invocation_id: str,
    start_time: float,
    webhook_id: str | None,
    request_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Handle async invocation."""
    job_id = str(uuid.uuid4())
    now_iso = datetime.now(UTC).isoformat()
    now_ts = int(time.time())
    webhook_url: str | None = None

    if webhook_id:
        registration = get_webhook_registration(tenant_context, webhook_id)
        if registration is None:
            return error_response(404, "NOT_FOUND", f"Webhook '{webhook_id}' not found", request_id)
        webhook_url = _coerce_optional_string(registration.get("callback_url"))
        if webhook_url is None:
            return error_response(
                500, "INTERNAL_ERROR", "Webhook registration missing callback URL", request_id
            )

    # 1. Create JOB record in DynamoDB (platform-jobs)
    job_record = JobRecord(
        job_id=job_id,
        tenant_id=tenant_context.tenant_id,
        app_id=tenant_context.app_id,
        agent_name=agent.agent_name,
        status=JobStatus.PENDING,
        created_at=now_iso,
        ttl=now_ts + JOB_TTL_SECONDS,
        webhook_id=webhook_id,
        webhook_url=webhook_url,
    )
    log_job(tenant_context, job_record)

    # 2. Trigger Runtime
    try:
        response = get_http_session().post(
            f"{url}/invocations", headers=headers, json=payload, timeout=2
        )
        response.raise_for_status()
    except requests.exceptions.ReadTimeout:
        # Expected for async trigger if it's fire-and-forget
        pass

    latency_ms = int((time.time() - start_time) * 1000)

    # 3. Log invocation
    log_invocation(
        tenant_context,
        agent,
        invocation_id,
        InvocationStatus.SUCCESS,
        latency_ms,
        InvocationMode.ASYNC,
        input_tokens=0,
        output_tokens=0,
        job_id=job_id,
        session_id=session_id or "async-session",
    )

    return {
        "statusCode": 202,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "jobId": job_id,
                "status": "accepted",
                "mode": "async",
                "pollUrl": f"/v1/jobs/{job_id}",
                "webhookDelivery": "registered" if webhook_url else "not_registered",
            }
        ),
    }


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
    """Write invocation audit record to DynamoDB using data-access-lib."""
    try:
        db = TenantScopedDynamoDB(tenant_context)
        now_iso = datetime.now(UTC).isoformat()
        now_ts = int(time.time())

        # Hot-partition mitigation (ADR-012)
        jitter = get_jitter()

        record = InvocationRecord(
            invocation_id=invocation_id,
            tenant_id=tenant_context.tenant_id,
            app_id=tenant_context.app_id,
            agent_name=agent.agent_name,
            agent_version=agent.version,
            session_id=session_id or "unknown-session",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            status=status,
            runtime_region=runtime_region or get_config()["runtime_region"],
            invocation_mode=mode,
            timestamp=now_iso,
            ttl=now_ts + INVOCATION_TTL_SECONDS,
            jitter=jitter,
            error_code=error_code,
            job_id=job_id,
        )

        item = {
            "PK": record.pk,
            "SK": record.sk,
            "invocation_id": record.invocation_id,
            "tenant_id": record.tenant_id,
            "app_id": record.app_id,
            "agent_name": record.agent_name,
            "agent_version": record.agent_version,
            "session_id": record.session_id,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "latency_ms": record.latency_ms,
            "status": str(record.status),
            "runtime_region": record.runtime_region,
            "invocation_mode": str(record.invocation_mode),
            "timestamp": record.timestamp,
            "ttl": record.ttl,
        }
        if record.jitter:
            item["jitter"] = record.jitter
        if record.job_id:
            item["job_id"] = record.job_id
        if record.error_code:
            item["error_code"] = record.error_code

        db.put_item(INVOCATIONS_TABLE, item)

        # Emit real-time metrics for observability (TASK-290)
        emit_invocation_metrics(
            tenant_context, agent, status, latency_ms, input_tokens, output_tokens
        )
    except Exception:
        logger.exception("Failed to log invocation")


def emit_invocation_metrics(
    tenant_context: TenantContext,
    agent: AgentRecord,
    status: InvocationStatus,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Emit real-time invocation metrics to CloudWatch."""
    try:
        cw = get_cloudwatch()

        # We emit two sets of metrics:
        # 1. Detailed: {TenantId, AgentName}
        # 2. Aggregate: {TenantId} (for the tenant dashboard)

        dimensions_sets = [
            [
                {"Name": "TenantId", "Value": tenant_context.tenant_id},
                {"Name": "AgentName", "Value": agent.agent_name},
            ],
            [
                {"Name": "TenantId", "Value": tenant_context.tenant_id},
            ],
        ]

        metric_data = []
        for dims in dimensions_sets:
            metric_data.extend(
                [
                    {
                        "MetricName": "Invocations",
                        "Value": 1.0,
                        "Unit": "Count",
                        "Dimensions": dims,
                    },
                    {
                        "MetricName": "Latency",
                        "Value": float(latency_ms),
                        "Unit": "Milliseconds",
                        "Dimensions": dims,
                    },
                    {
                        "MetricName": "InputTokens",
                        "Value": float(input_tokens),
                        "Unit": "Count",
                        "Dimensions": dims,
                    },
                    {
                        "MetricName": "OutputTokens",
                        "Value": float(output_tokens),
                        "Unit": "Count",
                        "Dimensions": dims,
                    },
                ]
            )

            if status != InvocationStatus.SUCCESS:
                metric_data.append(
                    {
                        "MetricName": "Errors",
                        "Value": 1.0,
                        "Unit": "Count",
                        "Dimensions": dims,
                    }
                )

        # CloudWatch PutMetricData has a limit of 1000 metrics per call
        # but here we have at most 10 metrics (5 names * 2 sets), so it's fine.
        cw.put_metric_data(Namespace="Platform/Bridge", MetricData=metric_data)
    except Exception as e:
        logger.warning(f"Failed to emit invocation metrics: {e}")


def emit_bedrock_throttle_metric(
    *,
    tenant_context: TenantContext,
    agent: AgentRecord,
    runtime_region: str,
) -> None:
    try:
        get_cloudwatch().put_metric_data(
            Namespace="Platform/Bridge",
            MetricData=[
                {
                    "MetricName": "Invocation.Throttled.Bedrock",
                    "Value": 1.0,
                    "Unit": "Count",
                    "Dimensions": [
                        {"Name": "TenantId", "Value": tenant_context.tenant_id},
                        {"Name": "AgentName", "Value": agent.agent_name},
                        {"Name": "RuntimeRegion", "Value": runtime_region},
                    ],
                }
            ],
        )
    except Exception as exc:
        logger.warning(f"Failed to emit Bedrock throttle metric: {exc}")


def log_job(tenant_context: TenantContext, record: JobRecord) -> None:
    """Write job record to DynamoDB."""
    try:
        db = TenantScopedDynamoDB(tenant_context)
        item = {
            "PK": record.pk,
            "SK": record.sk,
            "job_id": record.job_id,
            "tenant_id": record.tenant_id,
            "app_id": record.app_id,
            "agent_name": record.agent_name,
            "status": str(record.status),
            "created_at": record.created_at,
            "ttl": record.ttl,
        }
        if record.webhook_id:
            item["webhook_id"] = record.webhook_id
        if record.webhook_url:
            item["webhook_url"] = record.webhook_url
        item["webhook_delivered"] = bool(record.webhook_delivered)
        item["webhook_delivery_attempts"] = int(record.webhook_delivery_attempts)
        if record.webhook_delivery_status:
            item["webhook_delivery_status"] = record.webhook_delivery_status
        if record.webhook_delivery_error:
            item["webhook_delivery_error"] = record.webhook_delivery_error
        if record.webhook_last_attempt_at:
            item["webhook_last_attempt_at"] = record.webhook_last_attempt_at
        if record.started_at:
            item["started_at"] = record.started_at
        if record.completed_at:
            item["completed_at"] = record.completed_at
        if record.result_s3_key:
            item["result_s3_key"] = record.result_s3_key
        if record.error_message:
            item["error_message"] = record.error_message

        db.put_item(JOBS_TABLE, item)
    except Exception:
        logger.exception("Failed to log job")
