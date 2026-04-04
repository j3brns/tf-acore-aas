from __future__ import annotations

from typing import Any


def is_tier_allowed(tenant_tier: str, minimum_tier: str, *, tier_order: dict[str, int]) -> bool:
    tenant_rank = tier_order.get(tenant_tier, -1)
    minimum_rank = tier_order.get(minimum_tier, 100)
    return tenant_rank >= minimum_rank


def extract_minimum_tier(tool_record: dict[str, Any]) -> str:
    minimum = tool_record.get("tierMinimum")
    if minimum is None:
        minimum = tool_record.get("tier_minimum")
    if minimum is None:
        return "basic"
    return str(minimum)


def get_tool_record(
    tool_name: str,
    tenant_id: str,
    *,
    db_factory: Any,
    get_platform_context: Any,
    tools_table: str,
) -> dict[str, Any] | None:
    """Fetch tenant-specific tool first, then global."""
    db = db_factory(get_platform_context())
    primary_key = {"PK": f"TOOL#{tool_name}"}
    candidate_sort_keys = [f"TENANT#{tenant_id}", "GLOBAL"]
    for sort_key in candidate_sort_keys:
        item = db.get_item(tools_table, {**primary_key, "SK": sort_key}, ConsistentRead=True)
        if item and bool(item.get("enabled", False)):
            return dict(item)
    return None


def validate_tool_access(
    *,
    method: str,
    request_body: dict[str, Any],
    gateway_request: dict[str, Any],
    request_id: Any,
    tenant_id: str,
    tier: str,
    extract_tool_name: Any,
    get_tool_record: Any,
    get_capability_policy: Any,
    extract_minimum_tier: Any,
    is_tier_allowed: Any,
    error_response: Any,
    logger: Any,
) -> tuple[str | None, dict[str, Any] | None]:
    tool_name = extract_tool_name(method, request_body)
    if method != "tools/call":
        return tool_name, None

    if not tool_name:
        return None, error_response(
            gateway_request=gateway_request,
            request_id=request_id,
            status_code=400,
            code=-32600,
            message="tools/call missing params.name",
        )

    try:
        tool_record = get_tool_record(tool_name, tenant_id)
    except Exception:
        logger.exception("Failed to read tool registry", extra={"tool_name": tool_name})
        return tool_name, error_response(
            gateway_request=gateway_request,
            request_id=request_id,
            status_code=500,
            code=-32603,
            message="Failed to resolve tool policy",
        )

    if tool_record is None:
        return tool_name, error_response(
            gateway_request=gateway_request,
            request_id=request_id,
            status_code=403,
            code=-32003,
            message="Tool is unavailable for this tenant",
        )

    capability_policy = get_capability_policy()
    if capability_policy:
        if not capability_policy.is_enabled(
            f"tools.{tool_name}", tenant_id=tenant_id, tenant_tier=tier
        ):
            logger.warning(
                "Tool capability disabled",
                extra={"tenant_id": tenant_id, "capability": f"tools.{tool_name}"},
            )
            return tool_name, error_response(
                gateway_request=gateway_request,
                request_id=request_id,
                status_code=403,
                code=-32003,
                message=f"Tool '{tool_name}' is not enabled for this tenant",
            )

    minimum_tier = extract_minimum_tier(tool_record)
    if not is_tier_allowed(tier, minimum_tier):
        return tool_name, error_response(
            gateway_request=gateway_request,
            request_id=request_id,
            status_code=403,
            code=-32003,
            message="Tenant tier is insufficient for this tool",
        )

    return tool_name, None
