"""
gateway.interceptors.response_interceptor — AgentCore Gateway RESPONSE interceptor.

On every tool response:
  - tools/list: filters to tools where tierMinimum <= tenant tier
  - tools/call: scans response for PII patterns and redacts before returning
    PII patterns loaded from SSM /platform/gateway/pii-patterns/default
    Patterns: UK NI number, NHS number, sort code, account number, email

Implemented in TASK-037 / ISSUE-44.
ADRs: ADR-004
"""

import json
import os
import re
import time
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

from data_access import TenantScopedDynamoDB
from data_access.models import TenantContext, TenantTier

logger = Logger(service="gateway-response-interceptor")
tracer = Tracer()

# ---------------------------------------------------------------------------
# Constants and configuration
# ---------------------------------------------------------------------------
TOOLS_TABLE = os.environ.get("TOOLS_TABLE", "platform-tools")
PII_PATTERNS_PARAM = os.environ.get("PII_PATTERNS_PARAM", "/platform/gateway/pii-patterns/default")
PII_CACHE_TTL_SECONDS = 60
PII_REDACTION_TOKEN = "[REDACTED]"

DEFAULT_PII_PATTERN_STRINGS: tuple[str, ...] = (
    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",  # email
    r"[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s*\d{2}\s*\d{2}\s*\d{2}\s*[A-D]",  # UK NI
    r"\d{3}\s*\d{3}\s*\d{4}",  # NHS
    r"\d{2}-\d{2}-\d{2}",  # sort code
    r"\b\d{8}\b",  # account number
)

# ---------------------------------------------------------------------------
# Global clients and cache
# ---------------------------------------------------------------------------
_ssm_client = None
_pii_patterns: list[re.Pattern[str]] = []
_pii_cache_expiry: float = 0


def get_ssm() -> Any:
    global _ssm_client
    if _ssm_client is None:
        region = os.environ["AWS_REGION"]
        _ssm_client = boto3.client("ssm", region_name=region)
    return _ssm_client


def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern_str in patterns:
        try:
            compiled.append(re.compile(pattern_str, re.IGNORECASE))
        except re.error:
            logger.warning("Invalid PII regex pattern from SSM", pattern=pattern_str)
    return compiled


def _parse_patterns(raw_patterns: str | None) -> list[str]:
    if not raw_patterns:
        return []

    decoded = json.loads(raw_patterns)
    if isinstance(decoded, dict):
        return [value for value in decoded.values() if isinstance(value, str)]
    if isinstance(decoded, list):
        return [value for value in decoded if isinstance(value, str)]

    logger.warning("Unexpected PII patterns format in SSM parameter", type=type(decoded).__name__)
    return []


def load_pii_patterns() -> list[re.Pattern[str]]:
    """Fetch and compile PII redaction patterns from SSM with cache."""
    global _pii_patterns, _pii_cache_expiry
    now = time.time()
    if now < _pii_cache_expiry:
        return _pii_patterns

    try:
        ssm = get_ssm()
        response = ssm.get_parameter(Name=PII_PATTERNS_PARAM, WithDecryption=False)
        raw_patterns = response.get("Parameter", {}).get("Value")
        compiled = _compile_patterns(_parse_patterns(raw_patterns))
        if not compiled:
            logger.warning("PII pattern set empty, using built-in defaults")
            compiled = _compile_patterns(list(DEFAULT_PII_PATTERN_STRINGS))
        _pii_patterns = compiled
        _pii_cache_expiry = now + PII_CACHE_TTL_SECONDS
    except Exception:
        logger.exception("Failed to load PII patterns from SSM, using defaults")
        _pii_patterns = _compile_patterns(list(DEFAULT_PII_PATTERN_STRINGS))
        _pii_cache_expiry = now + PII_CACHE_TTL_SECONDS

    return _pii_patterns


def redact_pii(data: Any) -> Any:
    """Recursively redact PII from strings, lists, and dicts."""
    patterns = load_pii_patterns()

    if isinstance(data, str):
        redacted = data
        for pattern in patterns:
            redacted = pattern.sub(PII_REDACTION_TOKEN, redacted)
        return redacted
    if isinstance(data, dict):
        return {k: redact_pii(v) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_pii(v) for v in data]
    return data


def is_tier_sufficient(current_tier: TenantTier, required_tier: TenantTier) -> bool:
    """Check if the current tenant tier meets the tool's minimum tier requirement."""
    order = {TenantTier.BASIC: 0, TenantTier.STANDARD: 1, TenantTier.PREMIUM: 2}
    return order.get(current_tier, 0) >= order.get(required_tier, 0)


def _parse_tier(value: Any) -> TenantTier:
    if isinstance(value, TenantTier):
        return value
    try:
        return TenantTier(str(value).lower())
    except ValueError:
        return TenantTier.BASIC


def _resolve_tool_minimum_tier(
    *,
    tool: dict[str, Any],
    context: TenantContext,
    db: TenantScopedDynamoDB | None,
) -> TenantTier:
    payload_tier = tool.get("tierMinimum") or tool.get("tier_minimum")
    if payload_tier is not None:
        return _parse_tier(payload_tier)

    tool_name = tool.get("name")
    if not tool_name or db is None:
        return TenantTier.BASIC

    try:
        tool_record = db.get_item(TOOLS_TABLE, {"PK": f"TOOL#{tool_name}", "SK": "GLOBAL"})
        if not tool_record:
            tool_record = db.get_item(
                TOOLS_TABLE, {"PK": f"TOOL#{tool_name}", "SK": f"TENANT#{context.tenant_id}"}
            )
    except Exception:
        logger.exception("Unable to resolve tool tier from registry", tool_name=tool_name)
        return TenantTier.BASIC

    if not tool_record:
        return TenantTier.BASIC
    return _parse_tier(tool_record.get("tier_minimum"))


def filter_tools(body: dict[str, Any], context: TenantContext) -> dict[str, Any]:
    """Filter tools/list response to only include tools permitted for the tenant's tier."""
    tools = body.get("tools", [])
    if not isinstance(tools, list):
        return body

    db: TenantScopedDynamoDB | None = None
    try:
        db = TenantScopedDynamoDB(context)
    except Exception:
        logger.exception(
            "Failed to initialize tool registry client; using payload tier values only"
        )

    filtered_tools: list[Any] = []

    for tool in tools:
        if not isinstance(tool, dict):
            filtered_tools.append(tool)
            continue

        required_tier = _resolve_tool_minimum_tier(tool=tool, context=context, db=db)
        if is_tier_sufficient(context.tier, required_tier):
            filtered_tools.append(tool)

    return {**body, "tools": filtered_tools}


def _header_value(headers: dict[str, Any], key: str, default: Any = None) -> Any:
    direct = headers.get(key)
    if direct is not None:
        return direct
    lowered = key.lower()
    for header_key, header_value in headers.items():
        if isinstance(header_key, str) and header_key.lower() == lowered:
            return header_value
    return default


def _transform_gateway_body(body: Any, context: TenantContext) -> Any:
    parsed_body = body
    body_was_json_string = False

    if isinstance(body, str):
        try:
            parsed_body = json.loads(body)
            body_was_json_string = True
        except json.JSONDecodeError:
            parsed_body = body

    if isinstance(parsed_body, dict) and isinstance(parsed_body.get("tools"), list):
        transformed_body = filter_tools(parsed_body, context)
    else:
        transformed_body = redact_pii(parsed_body)

    if body_was_json_string:
        return json.dumps(transformed_body)
    return transformed_body


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """AgentCore Gateway RESPONSE interceptor entry point."""
    logger.info("Response interceptor triggered")

    mcp = event.get("mcp", {})
    if not isinstance(mcp, dict):
        mcp = {}
    gateway_response = mcp.get("gatewayResponse", {})
    if not isinstance(gateway_response, dict):
        gateway_response = {}
    body = gateway_response.get("body", {})
    headers = gateway_response.get("headers", {})
    if not isinstance(headers, dict):
        headers = {}

    # Extract tenant context from headers (injected by authoriser/bridge)
    tenant_id = _header_value(headers, "x-tenant-id")
    app_id = _header_value(headers, "x-app-id")
    tier_str = _header_value(headers, "x-tier", "basic")
    sub = _header_value(headers, "x-acting-sub", "unknown")

    if not tenant_id or not app_id:
        logger.warning("Missing tenant context headers in response interceptor")
        # Cannot filter/scope properly without context, but must return valid response
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {"transformedGatewayResponse": gateway_response},
        }

    tenant_tier = _parse_tier(tier_str)

    tenant_context = TenantContext(tenant_id=tenant_id, app_id=app_id, tier=tenant_tier, sub=sub)

    transformed_body = _transform_gateway_body(body, tenant_context)

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                **gateway_response,
                "body": transformed_body,
            }
        },
    }
