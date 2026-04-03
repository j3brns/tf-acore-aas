from __future__ import annotations

from typing import Any

from data_access import TenantScopedDynamoDB
from data_access.models import TenantContext, TenantTier


def is_tier_sufficient(current_tier: TenantTier, required_tier: TenantTier) -> bool:
    order = {TenantTier.BASIC: 0, TenantTier.STANDARD: 1, TenantTier.PREMIUM: 2}
    return order.get(current_tier, 0) >= order.get(required_tier, 0)


def parse_tier(value: Any) -> TenantTier:
    if isinstance(value, TenantTier):
        return value
    try:
        return TenantTier(str(value).lower())
    except ValueError:
        return TenantTier.BASIC


def resolve_tool_minimum_tier(
    *,
    tool: dict[str, Any],
    context: TenantContext,
    db: TenantScopedDynamoDB | None,
    tools_table: str,
    logger: Any,
) -> TenantTier:
    payload_tier = tool.get("tierMinimum") or tool.get("tier_minimum")
    if payload_tier is not None:
        return parse_tier(payload_tier)

    tool_name = tool.get("name")
    if not tool_name or db is None:
        return TenantTier.BASIC

    try:
        tool_record = db.get_item(tools_table, {"PK": f"TOOL#{tool_name}", "SK": "GLOBAL"})
        if not tool_record:
            tool_record = db.get_item(
                tools_table, {"PK": f"TOOL#{tool_name}", "SK": f"TENANT#{context.tenant_id}"}
            )
    except Exception:
        logger.exception("Unable to resolve tool tier from registry", tool_name=tool_name)
        return TenantTier.BASIC

    if not tool_record:
        return TenantTier.BASIC
    return parse_tier(tool_record.get("tier_minimum"))


def filter_tools(
    body: dict[str, Any],
    context: TenantContext,
    *,
    tools_table: str,
    logger: Any,
) -> dict[str, Any]:
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

        required_tier = resolve_tool_minimum_tier(
            tool=tool,
            context=context,
            db=db,
            tools_table=tools_table,
            logger=logger,
        )
        if is_tier_sufficient(context.tier, required_tier):
            filtered_tools.append(tool)

    return {**body, "tools": filtered_tools}
