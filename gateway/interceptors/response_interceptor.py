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

# ---------------------------------------------------------------------------
# Global clients and cache
# ---------------------------------------------------------------------------
_ssm_client = None
_pii_patterns: list[re.Pattern] = []
_pii_cache_expiry: float = 0


def get_ssm():
    global _ssm_client
    if _ssm_client is None:
        region = os.environ.get("AWS_REGION", "eu-west-2")
        _ssm_client = boto3.client("ssm", region_name=region)
    return _ssm_client


def load_pii_patterns() -> list[re.Pattern]:
    """Fetch and compile PII redaction patterns from SSM with cache."""
    global _pii_patterns, _pii_cache_expiry
    now = time.time()
    if now < _pii_cache_expiry:
        return _pii_patterns

    try:
        ssm = get_ssm()
        response = ssm.get_parameter(Name=PII_PATTERNS_PARAM)
        parameter = response.get("Parameter", {})
        patterns_json = parameter.get("Value")

        if not patterns_json:
            logger.warning("PII patterns SSM parameter is empty or missing Value")
            patterns_dict = {}
        else:
            patterns_dict = json.loads(patterns_json)

        new_patterns = []
        for name, pattern_str in patterns_dict.items():
            try:
                new_patterns.append(re.compile(pattern_str, re.IGNORECASE))
            except re.error:
                logger.error(
                    "Invalid regex pattern in SSM", extra={"name": name, "pattern": pattern_str}
                )

        _pii_patterns = new_patterns
        _pii_cache_expiry = now + 60  # 60s cache TTL
    except Exception:
        logger.exception("Failed to load PII patterns from SSM, using defaults")
        # Default patterns if SSM fails or is not configured
        defaults = {
            "email": r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
            "uk_ni": r"[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s*\d{2}\s*\d{2}\s*\d{2}\s*[A-D]",
            "uk_nhs": r"\d{3}\s*\d{3}\s*\d{4}",
            "sort_code": r"\d{2}-\d{2}-\d{2}",
            "account_number": r"\d{8}",
        }
        _pii_patterns = [re.compile(p, re.IGNORECASE) for p in defaults.values()]
        _pii_cache_expiry = now + 60

    return _pii_patterns


def redact_pii(data: Any) -> Any:
    """Recursively redact PII from strings, lists, and dicts."""
    patterns = load_pii_patterns()

    if isinstance(data, str):
        redacted = data
        for pattern in patterns:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted
    elif isinstance(data, dict):
        return {k: redact_pii(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [redact_pii(v) for v in data]
    else:
        return data


def is_tier_sufficient(current_tier: TenantTier, required_tier: TenantTier) -> bool:
    """Check if the current tenant tier meets the tool's minimum tier requirement."""
    order = {TenantTier.BASIC: 0, TenantTier.STANDARD: 1, TenantTier.PREMIUM: 2}
    return order.get(current_tier, 0) >= order.get(required_tier, 0)


def filter_tools(body: dict[str, Any], context: TenantContext) -> dict[str, Any]:
    """Filter tools/list response to only include tools permitted for the tenant's tier."""
    tools = body.get("tools", [])
    if not tools or not isinstance(tools, list):
        return body

    db = TenantScopedDynamoDB(context)
    filtered_tools = []

    for tool in tools:
        tool_name = tool.get("name")
        if not tool_name:
            continue

        # Check GLOBAL registry first, then tenant-specific registry
        tool_record_item = db.get_item(TOOLS_TABLE, {"PK": f"TOOL#{tool_name}", "SK": "GLOBAL"})
        if not tool_record_item:
            tool_record_item = db.get_item(
                TOOLS_TABLE, {"PK": f"TOOL#{tool_name}", "SK": f"TENANT#{context.tenant_id}"}
            )

        if tool_record_item:
            min_tier_str = tool_record_item.get("tier_minimum", "basic")
            try:
                min_tier = TenantTier(min_tier_str)
            except ValueError:
                logger.warning(
                    "Invalid tier_minimum in tool record",
                    extra={"tool": tool_name, "tier": min_tier_str},
                )
                min_tier = TenantTier.BASIC

            if is_tier_sufficient(context.tier, min_tier):
                filtered_tools.append(tool)
            else:
                logger.debug(
                    "Filtering tool due to insufficient tier",
                    extra={"tool": tool_name, "tier": context.tier, "min_tier": min_tier},
                )
        else:
            # If not in registry, allow it by default (log as warning for operator)
            logger.warning(
                "Tool not found in registry, allowing by default", extra={"tool_name": tool_name}
            )
            filtered_tools.append(tool)

    return {**body, "tools": filtered_tools}


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """AgentCore Gateway RESPONSE interceptor entry point."""
    logger.info("Response interceptor triggered")

    mcp = event.get("mcp", {})
    gateway_response = mcp.get("gatewayResponse", {})
    body = gateway_response.get("body", {})
    headers = gateway_response.get("headers", {})

    # Extract tenant context from headers (injected by authoriser/bridge)
    tenant_id = headers.get("x-tenant-id")
    app_id = headers.get("x-app-id")
    tier_str = headers.get("x-tier", "basic")
    sub = headers.get("x-acting-sub", "unknown")

    if not tenant_id or not app_id:
        logger.warning("Missing tenant context headers in response interceptor")
        # Cannot filter/scope properly without context, but must return valid response
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {"transformedGatewayResponse": gateway_response},
        }

    try:
        tenant_tier = TenantTier(tier_str)
    except ValueError:
        tenant_tier = TenantTier.BASIC

    tenant_context = TenantContext(tenant_id=tenant_id, app_id=app_id, tier=tenant_tier, sub=sub)

    # Detect method based on body structure (MCP standard)
    if "tools" in body and isinstance(body["tools"], list):
        # This is a tools/list response
        transformed_body = filter_tools(body, tenant_context)
    else:
        # This is a tools/call result (or other response), redact PII
        transformed_body = redact_pii(body)

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                **gateway_response,
                "body": transformed_body,
            }
        },
    }
