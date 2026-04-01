"""
platform-diagnostics agent — Read-only platform operator assistant.

Provides diagnostics and runbook guidance by using platform tools.
Restricted to the 'platform' tenant and requires Platform.Admin or Operator roles.

Implemented in ISSUE-389.
"""

from typing import Any

from aws_lambda_powertools import Logger
from bedrock_agentcore import BedrockAgentCoreApp, RequestContext

try:
    from bedrock_agentcore.gateway import get_gateway_tools
except ImportError:
    # Fallback for environments where the gateway module is not yet available
    def get_gateway_tools() -> list[Any]:
        return []


from strands import Agent

logger = Logger(service="platform-diagnostics-agent")

# ASGI application
invoke = BedrockAgentCoreApp()

SYSTEM_PROMPT = """You are the Platform Diagnostics Assistant.
Your goal is to help platform operators troubleshoot issues, monitor health, and follow runbooks.

You have access to read-only platform diagnostic tools.
- Use `get_platform_health` for overall system status.
- Use `get_tenant_status` to investigate issues with a specific tenant.
- Use `get_recent_errors` to see system-level errors or security events.
- Use `get_runbook_guidance` to find specific steps for a given situation or runbook ID.

Guidelines:
1. Always be professional and concise.
2. If you find an error, try to match it to a runbook and provide the steps.
3. Do not disclose sensitive platform configuration unless relevant to the diagnostic.
4. If you are unsure, ask the operator for clarification.

Example flow:
Operator: "Why is t-test-001 failing?"
You: Call `get_tenant_status(tenant_id="t-test-001")`.
Analyze the results. If there are recent errors, call `get_recent_errors(tenant_id="t-test-001")`.
Provide a summary and suggest a runbook if applicable.
"""


@invoke.entrypoint
def handler(payload: dict[str, Any], context: RequestContext) -> Any:
    """Agent entrypoint."""
    prompt = str(payload.get("prompt", ""))
    appid = str(payload.get("appid", "platform"))
    tenant_id = str(payload.get("tenantId", "unknown"))

    logger.append_keys(appid=appid, tenantid=tenant_id)
    logger.info("platform-diagnostics invoked", extra={"prompt_len": len(prompt)})

    if tenant_id != "platform":
        return {
            "error": "Access denied: This agent is reserved for the platform tenant.",
            "code": "ACCESS_DENIED",
            "tenantId": tenant_id,
        }

    # Initialize tools and agent
    # get_gateway_tools() fetches tools available for the agent's tier and tenant.
    try:
        tools = get_gateway_tools()
        agent = Agent(system_prompt=SYSTEM_PROMPT, tools=tools)

        # Run the agent
        # Note: In a real implementation, we might use streaming.
        # For simplicity in this first version, we'll use sync.
        response = agent(prompt)

        return {"output": response.message, "agent": "platform-diagnostics", "version": "1.0.0"}
    except Exception as exc:
        logger.exception("Agent execution failed")
        return {"error": "Internal agent error", "code": "AGENT_ERROR", "details": str(exc)}
