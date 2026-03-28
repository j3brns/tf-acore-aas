from __future__ import annotations

from typing import Any

from data_access.models import TenantContext, TenantTier


def handle_invoke_request(
    *,
    event: dict[str, Any],
    request_id: str,
    tenant_context: TenantContext,
    path: str,
    path_params: dict[str, Any],
    response_stream: Any,
    error_response: Any,
    parse_body: Any,
    coerce_optional_string: Any,
    is_invoke_contract_path: Any,
    get_agent_record: Any,
    get_capability_client: Any,
    invoke_agent: Any,
) -> Any:
    agent_name = coerce_optional_string(path_params.get("agentName"))
    if path and not is_invoke_contract_path(path, agent_name):
        return error_response(404, "NOT_FOUND", "Route not found", request_id)
    if not agent_name:
        return error_response(400, "INVALID_REQUEST", "Missing agentName in path", request_id)

    try:
        body = parse_body(event)
    except ValueError:
        return error_response(400, "INVALID_REQUEST", "Invalid JSON in request body", request_id)

    prompt = coerce_optional_string(body.get("input"))
    if not prompt:
        return error_response(400, "INVALID_REQUEST", "Missing 'input' in request body", request_id)

    session_id = coerce_optional_string(body.get("sessionId"))
    webhook_id = coerce_optional_string(body.get("webhookId"))

    agent = get_agent_record(agent_name)
    if not agent:
        return error_response(404, "NOT_FOUND", f"Agent '{agent_name}' not found", request_id)

    tier_order = {TenantTier.BASIC: 0, TenantTier.STANDARD: 1, TenantTier.PREMIUM: 2}
    if tier_order[tenant_context.tier] < tier_order[agent.tier_minimum]:
        return error_response(
            403, "FORBIDDEN", "Tenant tier insufficient for this agent", request_id
        )

    capability_client = get_capability_client()
    policy = capability_client.fetch_policy()

    if not policy.is_enabled(
        "agents.invoke",
        tenant_id=tenant_context.tenant_id,
        tenant_tier=tenant_context.tier,
    ):
        return error_response(403, "FORBIDDEN", "Agent invocation capability disabled", request_id)

    if not policy.is_enabled(
        f"agents.{agent_name}",
        tenant_id=tenant_context.tenant_id,
        tenant_tier=tenant_context.tier,
    ):
        return error_response(
            403,
            "FORBIDDEN",
            f"Access to agent '{agent_name}' is not enabled for this tenant",
            request_id,
        )

    return invoke_agent(
        agent, tenant_context, prompt, session_id, webhook_id, request_id, response_stream
    )
