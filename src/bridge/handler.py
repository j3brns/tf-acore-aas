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
from botocore.exceptions import ClientError
from data_access import TenantScopedDynamoDB, TenantScopedS3
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
TENANTS_TABLE = os.environ.get("TENANTS_TABLE", "platform-tenants")
AGENTS_TABLE = os.environ.get("AGENTS_TABLE", "platform-agents")
INVOCATIONS_TABLE = os.environ.get("INVOCATIONS_TABLE", "platform-invocations")
JOBS_TABLE = os.environ.get("JOBS_TABLE", "platform-jobs")
OPS_LOCKS_TABLE = os.environ.get("OPS_LOCKS_TABLE", "platform-ops-locks")
JOB_RESULTS_BUCKET = os.environ.get("JOB_RESULTS_BUCKET")

PLATFORM_ENV = os.environ.get("PLATFORM_ENV", "dev")
RUNTIME_REGION_PARAM = os.environ.get("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")
MOCK_RUNTIME_URL_PARAM = os.environ.get(
    "MOCK_RUNTIME_URL_PARAM", "/platform/config/mock-runtime-url"
)
TENANT_EXECUTION_ROLE_PARAM_TEMPLATE = os.environ.get(
    "TENANT_EXECUTION_ROLE_PARAM_TEMPLATE", "/platform/tenants/{tenant_id}/{env}/execution-role-arn"
)
JOB_RESULT_URL_EXPIRY_SECONDS = int(os.environ.get("JOB_RESULT_URL_EXPIRY_SECONDS", "3600"))
IAM_ROLE_ARN_PATTERN = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):iam::(?P<account_id>\d{12}):role/(?P<role_name>[\w+=,.@\-_/]+)$"
)

# TTL constants from models
INVOCATION_TTL_SECONDS = 90 * 24 * 60 * 60
JOB_TTL_SECONDS = 7 * 24 * 60 * 60
WEBHOOK_SIGNATURE_HEADER = "X-Platform-Signature"
WEBHOOK_SIGNATURE_ALGORITHM = "HMAC-SHA256"
VALID_WEBHOOK_EVENTS = {"job.completed", "job.failed"}

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


def get_config(force_refresh: bool = False) -> dict[str, Any]:
    """Fetch and cache configuration from SSM.

    Args:
        force_refresh: If True, bypass cache and fetch fresh from SSM.
    """
    global _config_cache, _config_cache_expiry
    now = time.time()
    if not force_refresh and now < _config_cache_expiry:
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
        if _config_cache:
            return _config_cache
        return {"runtime_region": "eu-west-1", "mock_runtime_url": None}


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


def _tenant_execution_role_param_name(tenant_id: str) -> str:
    return TENANT_EXECUTION_ROLE_PARAM_TEMPLATE.format(tenant_id=tenant_id, env=PLATFORM_ENV)


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


def get_jitter() -> str:
    """Generate a 2-character random hex suffix for hot-partition mitigation."""
    return secrets.token_hex(1)


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext, response_stream: Any = None) -> Any:
    """Bridge Lambda entry point."""
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
        return get_job_status(tenant_context, path_params, request_id)
    if method == "GET" and path.endswith("/v1/agents"):
        return list_agents(tenant_context)
    if (
        method == "GET"
        and _coerce_optional_string(path_params.get("agentName"))
        and not path.endswith("/invoke")
    ):
        return get_agent_detail(path_params, request_id)
    if method == "POST" and path.endswith("/v1/webhooks"):
        return register_webhook(event, tenant_context, request_id)
    if method == "DELETE" and _coerce_optional_string(path_params.get("webhookId")):
        return delete_webhook(tenant_context, path_params, request_id)

    # 3. Contracted invoke route: POST /v1/agents/{agentName}/invoke.
    if method != "POST":
        return error_response(404, "NOT_FOUND", "Route not found", request_id)

    agent_name = _coerce_optional_string(path_params.get("agentName"))
    if path and not _is_invoke_contract_path(path, agent_name):
        return error_response(404, "NOT_FOUND", "Route not found", request_id)
    if not agent_name:
        return error_response(400, "INVALID_REQUEST", "Missing agentName in path", request_id)

    # 4. Parse Request Body
    try:
        body = _parse_body(event)
    except ValueError:
        return error_response(400, "INVALID_REQUEST", "Invalid JSON in request body", request_id)

    prompt = _coerce_optional_string(body.get("input"))
    if not prompt:
        return error_response(400, "INVALID_REQUEST", "Missing 'input' in request body", request_id)

    session_id = _coerce_optional_string(body.get("sessionId"))
    webhook_id = _coerce_optional_string(body.get("webhookId"))

    # 5. Lookup Agent
    agent = get_agent_record(agent_name)
    if not agent:
        return error_response(404, "NOT_FOUND", f"Agent '{agent_name}' not found", request_id)

    # 6. Validate Tier
    tier_order = {TenantTier.BASIC: 0, TenantTier.STANDARD: 1, TenantTier.PREMIUM: 2}
    if tier_order[tenant_tier] < tier_order[agent.tier_minimum]:
        logger.warning(
            "Tier insufficient",
            extra={"tenant_tier": tenant_tier, "tier_minimum": agent.tier_minimum},
        )
        return error_response(
            403, "FORBIDDEN", "Tenant tier insufficient for this agent", request_id
        )

    # 7. Invoke Agent (with failover/retry logic)
    return invoke_agent(
        agent, tenant_context, prompt, session_id, webhook_id, request_id, response_stream
    )


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


def _job_key(tenant_id: str, job_id: str) -> dict[str, str]:
    return {"PK": f"TENANT#{tenant_id}", "SK": f"JOB#{job_id}"}


def _webhook_key(webhook_id: str) -> dict[str, str]:
    return {"PK": f"WEBHOOK#{webhook_id}", "SK": "METADATA"}


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


def _is_newer_agent_record(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    candidate_deployed_at = str(candidate.get("deployed_at", ""))
    current_deployed_at = str(current.get("deployed_at", ""))
    if candidate_deployed_at != current_deployed_at:
        return candidate_deployed_at > current_deployed_at
    return str(candidate.get("version", "")) > str(current.get("version", ""))


def list_agents(tenant_context: TenantContext) -> dict[str, Any]:
    ddb = get_dynamodb()
    table = ddb.Table(AGENTS_TABLE)
    items = table.scan().get("Items", [])

    latest_by_name: dict[str, dict[str, Any]] = {}
    for item in items:
        agent_name = _coerce_optional_string(item.get("agent_name"))
        if agent_name is None:
            continue
        existing = latest_by_name.get(agent_name)
        if existing is None or _is_newer_agent_record(item, existing):
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


def get_agent_detail(path_params: dict[str, Any], request_id: str) -> dict[str, Any]:
    agent_name = _coerce_optional_string(path_params.get("agentName"))
    if not agent_name:
        return error_response(400, "INVALID_REQUEST", "Missing agentName in path", request_id)

    ddb = get_dynamodb()
    table = ddb.Table(AGENTS_TABLE)
    response = table.query(KeyConditionExpression=Key("PK").eq(f"AGENT#{agent_name}"))
    items = response.get("Items", [])
    if not items:
        return error_response(404, "NOT_FOUND", f"Agent '{agent_name}' not found", request_id)

    sorted_items = sorted(
        items,
        key=lambda item: (str(item.get("deployed_at", "")), str(item.get("version", ""))),
        reverse=True,
    )
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
    tenant_context: TenantContext, path_params: dict[str, Any], request_id: str
) -> dict[str, Any]:
    job_id = _coerce_optional_string(path_params.get("jobId"))
    if not job_id:
        return error_response(400, "INVALID_REQUEST", "Missing jobId in path", request_id)

    db = TenantScopedDynamoDB(tenant_context)
    record = db.get_item(JOBS_TABLE, _job_key(tenant_context.tenant_id, job_id))

    if record is None:
        return error_response(404, "NOT_FOUND", f"Job '{job_id}' not found", request_id)

    result_url: str | None = None
    status = str(record.get("status", JobStatus.PENDING))
    result_key = _coerce_optional_string(record.get("result_s3_key"))
    if status == str(JobStatus.COMPLETED) and result_key:
        try:
            result_url = _presigned_result_url(tenant_context, result_key)
        except ValueError as exc:
            return error_response(500, "INTERNAL_ERROR", str(exc), request_id)
        except Exception:
            logger.exception(
                "Failed to generate job result presigned URL",
                extra={"job_id": job_id},
            )
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
            }
        ),
    }


def _presigned_result_url(tenant_context: TenantContext, result_s3_key: str) -> str:
    bucket = _coerce_optional_string(JOB_RESULTS_BUCKET)
    if bucket is None:
        raise ValueError("JOB_RESULTS_BUCKET is not configured")

    expires_in = max(1, JOB_RESULT_URL_EXPIRY_SECONDS)
    tenant_s3 = TenantScopedS3(tenant_context)
    return tenant_s3.generate_presigned_url(
        bucket,
        result_s3_key,
        expires_in=expires_in,
    )


def register_webhook(
    event: dict[str, Any], tenant_context: TenantContext, request_id: str
) -> dict[str, Any]:
    try:
        body = _parse_body(event)
    except ValueError:
        return error_response(400, "INVALID_REQUEST", "Invalid JSON in request body", request_id)

    callback_url = _coerce_optional_string(body.get("callbackUrl"))
    if callback_url is None:
        return error_response(400, "INVALID_REQUEST", "callbackUrl is required", request_id)

    parsed_url = urllib.parse.urlparse(callback_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return error_response(
            422,
            "UNPROCESSABLE_ENTITY",
            "callbackUrl must be a valid URL",
            request_id,
        )

    events_raw = body.get("events")
    if not isinstance(events_raw, list) or not events_raw:
        return error_response(
            400,
            "INVALID_REQUEST",
            "events must be a non-empty array",
            request_id,
        )

    normalized_events: list[str] = []
    seen_events: set[str] = set()
    for raw_event in events_raw:
        event_name = _coerce_optional_string(raw_event)
        if event_name is None:
            return error_response(
                422,
                "UNPROCESSABLE_ENTITY",
                "events must contain non-empty values",
                request_id,
            )
        if event_name not in VALID_WEBHOOK_EVENTS:
            return error_response(
                422,
                "UNPROCESSABLE_ENTITY",
                f"Unsupported webhook event '{event_name}'",
                request_id,
            )
        if event_name in seen_events:
            return error_response(
                400,
                "INVALID_REQUEST",
                "events must not contain duplicate values",
                request_id,
            )
        seen_events.add(event_name)
        normalized_events.append(event_name)

    description = _coerce_optional_string(body.get("description"))
    if description and len(description) > 256:
        return error_response(
            422,
            "UNPROCESSABLE_ENTITY",
            "description must be 256 characters or fewer",
            request_id,
        )

    webhook_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()
    webhook_secret = secrets.token_urlsafe(32)

    item: dict[str, Any] = {
        "PK": _webhook_key(webhook_id)["PK"],
        "SK": "METADATA",
        "webhook_id": webhook_id,
        "tenant_id": tenant_context.tenant_id,
        "app_id": tenant_context.app_id,
        "callback_url": callback_url,
        "events": normalized_events,
        "created_at": created_at,
        "signature_secret": webhook_secret,
        "signature_header": WEBHOOK_SIGNATURE_HEADER,
        "signature_algorithm": WEBHOOK_SIGNATURE_ALGORITHM,
        "record_type": "webhook_registration",
    }
    if description:
        item["description"] = description

    db = TenantScopedDynamoDB(tenant_context)
    db.put_item(JOBS_TABLE, item)

    return {
        "statusCode": 201,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "webhookId": webhook_id,
                "callbackUrl": callback_url,
                "events": normalized_events,
                "createdAt": created_at,
                "signatureHeader": WEBHOOK_SIGNATURE_HEADER,
                "signatureAlgorithm": WEBHOOK_SIGNATURE_ALGORITHM,
            }
        ),
    }


def delete_webhook(
    tenant_context: TenantContext, path_params: dict[str, Any], request_id: str
) -> dict[str, Any]:
    webhook_id = _coerce_optional_string(path_params.get("webhookId"))
    if not webhook_id:
        return error_response(400, "INVALID_REQUEST", "Missing webhookId in path", request_id)

    key = _webhook_key(webhook_id)
    db = TenantScopedDynamoDB(tenant_context)
    existing = db.get_item(JOBS_TABLE, key)
    if existing is None or str(existing.get("tenant_id", "")) != tenant_context.tenant_id:
        return error_response(404, "NOT_FOUND", f"Webhook '{webhook_id}' not found", request_id)

    db.delete_item(JOBS_TABLE, key)
    return {"statusCode": 204, "headers": {}, "body": ""}


def get_webhook_registration(
    tenant_context: TenantContext, webhook_id: str
) -> dict[str, Any] | None:
    key = _webhook_key(webhook_id)
    db = TenantScopedDynamoDB(tenant_context)
    record = db.get_item(JOBS_TABLE, key)
    if record is None:
        return None
    if str(record.get("tenant_id", "")) != tenant_context.tenant_id:
        return None
    if _coerce_optional_string(record.get("callback_url")) is None:
        return None
    return record


def invoke_agent(
    agent: AgentRecord,
    tenant_context: TenantContext,
    prompt: str,
    session_id: str | None,
    webhook_id: str | None,
    request_id: str,
    response_stream: Any,
) -> Any:
    """Invoke the agent with failover and retry logic."""
    config = get_config()
    mock_url = config.get("mock_runtime_url")

    try:
        if mock_url:
            return invoke_mock_runtime(
                mock_url,
                agent,
                tenant_context,
                prompt,
                session_id,
                webhook_id,
                request_id,
                response_stream,
            )
        else:
            return invoke_real_runtime(
                config["runtime_region"],
                agent,
                tenant_context,
                prompt,
                session_id,
                webhook_id,
                request_id,
                response_stream,
            )
    except Exception as e:
        # Check if it's a 503 or ServiceUnavailableException
        is_unavailable = False
        if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code == 503:
            is_unavailable = True
        # Add real AWS exception check here in Phase 3

        if is_unavailable:
            logger.warning("Runtime unavailable, attempting failover")
            new_region = trigger_failover(config["runtime_region"])
            # Update config for retry
            config = get_config(force_refresh=True)
            mock_url = config.get("mock_runtime_url")

            # Retry once
            if mock_url:
                return invoke_mock_runtime(
                    mock_url,
                    agent,
                    tenant_context,
                    prompt,
                    session_id,
                    webhook_id,
                    request_id,
                    response_stream,
                )
            else:
                return invoke_real_runtime(
                    new_region,
                    agent,
                    tenant_context,
                    prompt,
                    session_id,
                    webhook_id,
                    request_id,
                    response_stream,
                )

        logger.exception("Invocation failed")
        return error_response(
            502, "BAD_GATEWAY", "Failed to communicate with agent runtime", request_id
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
) -> Any:
    """Invoke the real AgentCore Runtime (Phase 3)."""
    # 1. Get tenant record to find account_id
    tenant = get_tenant_record(tenant_context)
    if not tenant:
        return error_response(500, "INTERNAL_ERROR", "Tenant record not found", request_id)

    account_id = tenant.get("account_id") or tenant.get("accountId")
    if not account_id:
        return error_response(500, "INTERNAL_ERROR", "Tenant account_id not configured", request_id)

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
        return error_response(500, "INTERNAL_ERROR", str(exc), request_id)
    except Exception:
        return error_response(
            500, "INTERNAL_ERROR", "Failed to resolve tenant execution role ARN", request_id
        )

    if not execution_role_arn:
        return error_response(
            500, "INTERNAL_ERROR", "Tenant execution role ARN not configured", request_id
        )

    # 2. Assume tenant role
    try:
        # returns credentials; will be used in Phase 3 to initialize SDK client
        assume_tenant_role(tenant_context.tenant_id, execution_role_arn)
        logger.info(
            "Assumed tenant role",
            extra={
                "account_id": account_id_str,
                "region": region,
                "role_arn": execution_role_arn,
            },
        )
    except Exception:
        return error_response(500, "INTERNAL_ERROR", "Failed to assume tenant role", request_id)

    # 3. Invoke with SDK (Phase 3)
    # TODO: Implement with bedrock-agentcore SDK
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
    response = requests.post(f"{url}/invocations", headers=headers, json=payload, timeout=900)
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
        agent_name=agent.agent_name,
        status=JobStatus.PENDING,
        created_at=now_iso,
        ttl=now_ts + JOB_TTL_SECONDS,
        webhook_url=webhook_url,
    )
    log_job(tenant_context, job_record)

    # 2. Trigger Runtime
    try:
        response = requests.post(f"{url}/invocations", headers=headers, json=payload, timeout=2)
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
    job_id: str | None = None,
    session_id: str | None = None,
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
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            status=status,
            runtime_region=get_config()["runtime_region"],
            invocation_mode=mode,
            timestamp=now_iso,
            ttl=now_ts + INVOCATION_TTL_SECONDS,
            jitter=jitter,
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
        item["webhook_delivered"] = bool(record.webhook_delivered)
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
