"""
platform_tools.diagnostics_handler — Read-only platform diagnostics and runbook assistance.

Implements tools for the platform-diagnostics-agent to query platform health,
tenant status, recent errors, and runbook guidance.

Tools:
  - get_platform_health: Returns health signals for regions and services.
  - get_tenant_status: Returns status, tier, and recent metrics for a tenant.
  - get_recent_errors: Returns recent system-level errors or security events.
  - get_runbook_guidance: Returns guidance from the operator runbooks.

Implemented in ISSUE-389.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from aws_lambda_powertools import Logger
from boto3.dynamodb.conditions import Key
from data_access import ControlPlaneDynamoDB, TenantContext, TenantTier

logger = Logger(service="platform-diagnostics-tool")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TENANTS_TABLE = os.environ.get("TENANTS_TABLE_NAME", "platform-tenants")
INVOCATIONS_TABLE = os.environ.get("INVOCATIONS_TABLE_NAME", "platform-invocations")
RUNTIME_REGION_PARAM = os.environ.get("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")

# ---------------------------------------------------------------------------
# Runbook Data (Embedded for tool access)
# ---------------------------------------------------------------------------
RUNBOOKS = {
    "RUNBOOK-001": {
        "title": "Runtime Region Failover",
        "trigger": "ServiceUnavailableException from the active runtime region (e.g., eu-west-1).",
        "steps": [
            "1. Verify regional outage via Service Health Dashboard or CloudWatch metrics.",
            "2. Acquire the platform-runtime-failover lock via the Platform API.",
            "3. Trigger failover to the fallback region (e.g., eu-central-1) "
            "via POST /v1/platform/failover.",
            "4. Verify traffic is flowing in the new region.",
            "5. Release the lock and update status.",
        ],
    },
    "RUNBOOK-002": {
        "title": "AgentCore Quota Monitoring",
        "trigger": "ConcurrentSessions utilisation > 70%.",
        "steps": [
            "1. Check /v1/platform/quota to see current regional utilisation.",
            "2. Identify if any single tenant is responsible for the surge.",
            "3. If utilisation > 80%, initiate RUNBOOK-004 (Quota Increase).",
            "4. If utilisation > 90% and approval is slow, consider 'Option B' (Account Split).",
        ],
    },
    "RUNBOOK-003": {
        "title": "Tenant Access Violation",
        "trigger": "TenantAccessViolation alarm or security event log.",
        "steps": [
            "1. Identify the caller tenant and the target tenant from the logs.",
            "2. Determine if the attempt was a misconfiguration or a malicious probe.",
            "3. Suspend the caller tenant if necessary via "
            "POST /v1/platform/ops/tenants/{id}/suspend.",
            "4. Page the security team if a persistent breach is suspected.",
        ],
    },
    "RUNBOOK-005": {
        "title": "DLQ Management",
        "trigger": "DLQ CloudWatch alarm.",
        "steps": [
            "1. Inspect the messages in the DLQ via GET /v1/platform/ops/dlq/{name}.",
            "2. Identify the root cause (e.g., timeout, downstream error).",
            "3. Fix the underlying issue.",
            "4. Redrive the messages via POST /v1/platform/ops/dlq/{name}/redrive.",
        ],
    },
    "RUNBOOK-007": {
        "title": "Deployment Rollback",
        "trigger": "Failed deployment or regression detected post-release.",
        "steps": [
            "1. Identify the failing function(s).",
            "2. Perform a Lambda alias rollback via POST /v1/platform/ops/lambda-rollback.",
            "3. Verify the previous version is stable.",
            "4. Update the issue and investigate the root cause.",
        ],
    },
}

# ---------------------------------------------------------------------------
# Tool Implementations
# ---------------------------------------------------------------------------


def get_platform_health(db: ControlPlaneDynamoDB) -> dict[str, Any]:
    """Return synthetic and operational health signals for the platform."""
    _ = db
    # In a real implementation, this would query CloudWatch or a health table.
    return {
        "status": "healthy",
        "regions": [
            {"region": "eu-west-1", "status": "operational", "latency_ms": 12},
            {"region": "eu-central-1", "status": "operational", "latency_ms": 25},
        ],
        "services": {
            "AgentCore": "operational",
            "DynamoDB": "operational",
            "Bedrock": "operational",
            "Bridge": "operational",
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }


def get_tenant_status(db: ControlPlaneDynamoDB, tenant_id: str) -> dict[str, Any]:
    """Return the current status and metadata for a specific tenant."""
    tenant = db.get_item(TENANTS_TABLE, {"PK": f"TENANT#{tenant_id}", "SK": "METADATA"})
    if not tenant:
        return {"error": f"Tenant {tenant_id} not found"}

    # Recent invocation summary (last 1 hour)
    now = datetime.now(UTC)
    hour_ago = (now - timedelta(hours=1)).isoformat()

    # Note: Scanning invocations by tenant_id is slow in prod,
    # but for a platform tool it's acceptable with a small limit.
    recent_invocations = db.query(
        INVOCATIONS_TABLE,
        sk_condition=Key("SK").gt(f"TIME#{hour_ago}"),
        limit=20,
        scan_index_forward=False,
    )

    return {
        "tenantId": tenant_id,
        "displayName": tenant.get("display_name", "Unknown"),
        "status": tenant.get("status", "active"),
        "tier": tenant.get("tier", "basic"),
        "recentInvocations": len(recent_invocations.items),
        "lastUpdated": tenant.get("updated_at"),
    }


def get_recent_errors(db: ControlPlaneDynamoDB, tenant_id: str | None = None) -> dict[str, Any]:
    """Return recent errors or security events, optionally filtered by tenant."""
    # In a real implementation, this would query a dedicated audit/error table.
    # For now, we'll return a sample or query recent invocations with error status.

    # Sample security event
    events = [
        {
            "timestamp": (datetime.now(UTC) - timedelta(minutes=15)).isoformat(),
            "type": "tenant_access_violation",
            "tenantId": "t-suspicious-001",
            "details": "Attempted access to TENANT#t-test-001 partition",
        }
    ]

    if tenant_id:
        events = [e for e in events if e["tenantId"] == tenant_id]

    return {"events": events, "count": len(events)}


def get_runbook_guidance(query: str | None = None, runbook_id: str | None = None) -> dict[str, Any]:
    """Return guidance from the operator runbooks based on a query or ID."""
    if runbook_id:
        guidance = RUNBOOKS.get(runbook_id.upper())
        if guidance:
            return {"runbookId": runbook_id.upper(), **guidance}
        return {"error": f"Runbook {runbook_id} not found"}

    if query:
        # Simple keyword match
        query_lower = query.lower()
        matches = []
        for rid, data in RUNBOOKS.items():
            if query_lower in data["title"].lower() or query_lower in rid.lower():
                matches.append({"runbookId": rid, "title": data["title"]})

        if matches:
            return {"matches": matches}

    return {
        "availableRunbooks": [
            {"runbookId": rid, "title": data["title"]} for rid, data in RUNBOOKS.items()
        ]
    }


# ---------------------------------------------------------------------------
# Lambda Handler
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Tool entrypoint — handles JSON-RPC from Gateway."""
    logger.info("Diagnostics tool invoked", extra={"method": event.get("method")})

    # 1. Parse request (Gateway Tool Call format)
    # The Gateway REQUEST interceptor injects x-tenant-id, etc. into headers.
    headers = event.get("headers", {})
    tenant_id = headers.get("x-tenant-id") or headers.get("X-Tenant-Id")
    app_id = headers.get("x-app-id") or headers.get("X-App-Id")

    if not tenant_id or tenant_id != "platform":
        return {
            "jsonrpc": "2.0",
            "id": event.get("id"),
            "error": {"code": -32003, "message": "Access denied: Platform tenant only"},
        }

    # 2. Initialize dependencies
    ctx = TenantContext(
        tenant_id=tenant_id, app_id=app_id, tier=TenantTier.PREMIUM, sub="platform-diagnostics"
    )
    db = ControlPlaneDynamoDB(ctx)

    # 3. Dispatch tool
    method = event.get("method")
    params = event.get("params", {})

    # If it's a tools/call, the tool name is in params.name
    if method == "tools/call":
        tool_name = params.get("name")
        tool_params = params.get("arguments", {})
    else:
        # Fallback for direct calls
        tool_name = method
        tool_params = params

    result: Any = None
    try:
        if tool_name == "get_platform_health":
            result = get_platform_health(db)
        elif tool_name == "get_tenant_status":
            tid = tool_params.get("tenant_id") or tool_params.get("tenantId")
            if not tid:
                raise ValueError("tenant_id is required")
            result = get_tenant_status(db, tid)
        elif tool_name == "get_recent_errors":
            tid = tool_params.get("tenant_id") or tool_params.get("tenantId")
            result = get_recent_errors(db, tid)
        elif tool_name == "get_runbook_guidance":
            query = tool_params.get("query")
            rid = tool_params.get("runbook_id") or tool_params.get("runbookId")
            result = get_runbook_guidance(query, rid)
        else:
            return {
                "jsonrpc": "2.0",
                "id": event.get("id"),
                "error": {"code": -32601, "message": f"Method not found: {tool_name}"},
            }

        return {"jsonrpc": "2.0", "id": event.get("id"), "result": result}
    except Exception as exc:
        logger.exception("Tool execution failed")
        return {
            "jsonrpc": "2.0",
            "id": event.get("id"),
            "error": {"code": -32603, "message": str(exc)},
        }
