"""
bridge.handler — Agent invocation bridge Lambda.

Reads invocation_mode from agent registry, assumes tenant execution role,
and routes to AgentCore Runtime via sync, streaming, or async paths.

ADRs: ADR-003, ADR-005, ADR-009, ADR-010
"""

import json
import os
import time
import uuid
from datetime import UTC, datetime, timezone
from typing import Any

import boto3
import requests
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from data_access import TenantScopedDynamoDB
from data_access.models import (
    AgentRecord,
    InvocationMode,
    InvocationRecord,
    InvocationStatus,
    JobRecord,
    JobStatus,
    TenantContext,
    TenantTier,
)

logger = Logger(service="bridge")
tracer = Tracer()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AGENTS_TABLE = os.environ.get("AGENTS_TABLE", "platform-agents")
INVOCATIONS_TABLE = os.environ.get("INVOCATIONS_TABLE", "platform-invocations")
JOBS_TABLE = os.environ.get("JOBS_TABLE", "platform-jobs")
RUNTIME_REGION_PARAM = os.environ.get("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")
MOCK_RUNTIME_URL_PARAM = os.environ.get(
    "MOCK_RUNTIME_URL_PARAM", "/platform/config/mock-runtime-url"
)

# TTL constants from models
INVOCATION_TTL_SECONDS = 90 * 24 * 60 * 60
JOB_TTL_SECONDS = 7 * 24 * 60 * 60

# ---------------------------------------------------------------------------
# Global clients/cache
# ---------------------------------------------------------------------------
_ssm_client = None
_sts_client = None
_dynamodb_resource = None

# Cache for SSM parameters (60s TTL as per ARCHITECTURE.md)
_config_cache: dict[str, Any] = {}
_config_cache_expiry: float = 0


def get_ssm():
    global _ssm_client
    if _ssm_client is None:
        region = os.environ.get("AWS_REGION", "eu-west-2")
        _ssm_client = boto3.client("ssm", region_name=region)
    return _ssm_client


def get_sts():
    global _sts_client
    if _sts_client is None:
        region = os.environ.get("AWS_REGION", "eu-west-2")
        _sts_client = boto3.client("sts", region_name=region)
    return _sts_client


def get_dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        region = os.environ.get("AWS_REGION", "eu-west-2")
        _dynamodb_resource = boto3.resource("dynamodb", region_name=region)
    return _dynamodb_resource


def get_config() -> dict[str, Any]:
    """Fetch and cache configuration from SSM."""
    global _config_cache, _config_cache_expiry
    now = time.time()
    if now < _config_cache_expiry:
        return _config_cache

    try:
        ssm = get_ssm()
        # Fetch both runtime region and mock URL
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
        _config_cache_expiry = now + 60  # 60s cache TTL
        return _config_cache
    except Exception:
        logger.exception("Failed to fetch config from SSM")
        # Return stale cache if available, else defaults
        return _config_cache or {"runtime_region": "eu-west-1", "mock_runtime_url": None}


def get_agent_record(agent_name: str, version: str | None = None) -> AgentRecord | None:
    """Fetch agent metadata from the registry."""
    try:
        ddb = get_dynamodb()
        table = ddb.Table(AGENTS_TABLE)

        if not version:
            # Query for latest version
            response = table.query(
                KeyConditionExpression=Key("PK").eq(f"AGENT#{agent_name}"),
                ScanIndexForward=False,
                Limit=1,
            )
            items = response.get("Items", [])
            if not items:
                return None
            item = items[0]
        else:
            response = table.get_item(Key={"PK": f"AGENT#{agent_name}", "SK": f"VERSION#{version}"})
            item = response.get("Item")

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
            runtime_arn=str(item["runtime_arn"]) if item.get("runtime_arn") else None,
            estimated_duration_seconds=int(item["estimated_duration_seconds"])  # type: ignore
            if item.get("estimated_duration_seconds")
            else None,
        )
    except Exception:
        logger.exception("Failed to fetch agent record", extra={"agent_name": agent_name})
        return None


def error_response(status_code: int, code: str, message: str, request_id: str) -> dict[str, Any]:
    """Return a standard error response as per openapi.yaml."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": {"code": code, "message": message, "requestId": request_id}}),
    }


def assume_tenant_role(tenant_id: str, account_id: str) -> dict[str, Any] | None:
    """Assume the tenant's execution role via STS.

    Returns temporary credentials, or None if in local/mock mode.
    """
    if os.environ.get("MOCK_RUNTIME") == "true":
        return None

    role_arn = f"arn:aws:iam::{account_id}:role/platform-tenant-{tenant_id}-role"
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


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext, response_stream: Any = None) -> Any:
    """Bridge Lambda entry point."""
    request_id = context.aws_request_id

    # 1. Parse Authorizer Context
    auth_context = event.get("requestContext", {}).get("authorizer", {})
    tenant_id = auth_context.get("tenantid")
    app_id = auth_context.get("appid")
    tier_str = auth_context.get("tier", "basic")
    sub = auth_context.get("sub", "unknown")

    if not tenant_id or not app_id:
        logger.error("Missing tenant context in authorizer")
        return error_response(401, "UNAUTHENTICATED", "Missing tenant context", request_id)

    tenant_tier = TenantTier(tier_str)
    tenant_context = TenantContext(tenant_id=tenant_id, app_id=app_id, tier=tenant_tier, sub=sub)

    # Inject context into logs
    logger.append_keys(tenant_id=tenant_id, app_id=app_id)

    # 2. Parse Path Parameters
    path_params = event.get("pathParameters", {})
    agent_name = path_params.get("agentName")
    if not agent_name:
        return error_response(400, "INVALID_REQUEST", "Missing agentName in path", request_id)

    # 3. Parse Request Body
    try:
        body = json.loads(event.get("body", "{}"))
        prompt = body.get("input")
        session_id = body.get("sessionId")
        webhook_id = body.get("webhookId")
        if not prompt:
            return error_response(
                400, "INVALID_REQUEST", "Missing 'input' in request body", request_id
            )
    except json.JSONDecodeError:
        return error_response(400, "INVALID_REQUEST", "Invalid JSON in request body", request_id)

    # 4. Lookup Agent
    agent = get_agent_record(agent_name)
    if not agent:
        return error_response(404, "NOT_FOUND", f"Agent '{agent_name}' not found", request_id)

    # 5. Validate Tier
    tier_order = {TenantTier.BASIC: 0, TenantTier.STANDARD: 1, TenantTier.PREMIUM: 2}
    if tier_order[tenant_tier] < tier_order[agent.tier_minimum]:
        logger.warning(
            "Tier insufficient",
            extra={"tenant_tier": tenant_tier, "tier_minimum": agent.tier_minimum},
        )
        return error_response(
            403, "FORBIDDEN", "Tenant tier insufficient for this agent", request_id
        )

    # 6. Get Config (Region / Mock URL)
    config = get_config()

    # 7. Invoke Agent
    if config.get("mock_runtime_url"):
        return invoke_mock_runtime(
            config["mock_runtime_url"],
            agent,
            tenant_context,
            prompt,
            session_id,
            webhook_id,
            request_id,
            response_stream,
        )
    else:
        # Real AgentCore Runtime invocation
        # TODO: Implement in Phase 3
        return error_response(
            501, "NOT_IMPLEMENTED", "Real Runtime invocation not yet implemented", request_id
        )


def invoke_mock_runtime(
    url: str,
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any,
) -> Any:
    """Invoke the mock runtime via HTTP."""
    invocation_id = str(uuid.uuid4())
    start_time = time.time()

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

    try:
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
            )
        elif agent.invocation_mode == InvocationMode.ASYNC:
            # Handle async mode
            return handle_async_invocation(
                url, headers, payload, agent, tenant_context, invocation_id, start_time, webhook_id
            )
        else:
            # Default to sync mode
            return handle_sync_invocation(
                url, headers, payload, agent, tenant_context, invocation_id, start_time
            )
    except Exception:
        logger.exception("Failed to invoke mock runtime")
        return error_response(
            502, "BAD_GATEWAY", "Failed to communicate with agent runtime", request_id
        )


def handle_sync_invocation(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    agent: AgentRecord,
    tenant_context: TenantContext,
    invocation_id: str,
    start_time: float,
) -> dict[str, Any]:
    """Handle synchronous invocation."""
    response = requests.post(f"{url}/invocations", headers=headers, json=payload, timeout=900)
    response.raise_for_status()

    # Mock runtime returns SSE, collect into full text
    full_text = ""
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
                "sessionId": "mock-session-id",
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
) -> Any:
    """Handle streaming invocation using Lambda Response Streaming."""
    if not response_stream:
        logger.error("Streaming requested but response_stream not available")
        return error_response(
            500, "INTERNAL_ERROR", "Response streaming not enabled for this Lambda", request_id
        )

    with requests.post(
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
) -> dict[str, Any]:
    """Handle async invocation."""
    job_id = str(uuid.uuid4())
    now_iso = datetime.now(UTC).isoformat()
    now_ts = int(time.time())

    # 1. Create JOB record in DynamoDB (platform-jobs)
    job_record = JobRecord(
        job_id=job_id,
        tenant_id=tenant_context.tenant_id,
        agent_name=agent.agent_name,
        status=JobStatus.PENDING,
        created_at=now_iso,
        ttl=now_ts + JOB_TTL_SECONDS,
        webhook_url=webhook_id,
    )
    log_job(tenant_context, job_record)

    # 2. Trigger Runtime
    try:
        requests.post(f"{url}/invocations", headers=headers, json=payload, timeout=2)
    except requests.exceptions.ReadTimeout:
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
        job_id=job_id,
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
                "webhookDelivery": "registered" if webhook_id else "not_registered",
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
    job_id: str | None = None,
) -> None:
    """Write invocation audit record to DynamoDB using data-access-lib."""
    try:
        db = TenantScopedDynamoDB(tenant_context)
        now_iso = datetime.now(UTC).isoformat()
        now_ts = int(time.time())

        record = InvocationRecord(
            invocation_id=invocation_id,
            tenant_id=tenant_context.tenant_id,
            app_id=tenant_context.app_id,
            agent_name=agent.agent_name,
            agent_version=agent.version,
            session_id="mock-session",
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            status=status,
            runtime_region="eu-west-1",
            invocation_mode=mode,
            timestamp=now_iso,
            ttl=now_ts + INVOCATION_TTL_SECONDS,
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
        if record.job_id:
            item["job_id"] = record.job_id

        db.put_item(INVOCATIONS_TABLE, item)
    except Exception:
        logger.exception("Failed to log invocation")


def log_job(tenant_context: TenantContext, record: JobRecord) -> None:
    """Write job record to DynamoDB."""
    try:
        db = TenantScopedDynamoDB(tenant_context)
        item = {
            "PK": record.pk,
            "SK": record.sk,
            "job_id": record.job_id,
            "tenant_id": record.tenant_id,
            "agent_name": record.agent_name,
            "status": str(record.status),
            "created_at": record.created_at,
            "ttl": record.ttl,
        }
        if record.webhook_url:
            item["webhook_url"] = record.webhook_url

        db.put_item(JOBS_TABLE, item)
    except Exception:
        logger.exception("Failed to log job")
