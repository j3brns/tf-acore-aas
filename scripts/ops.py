"""
ops.py — Platform operations CLI.

All commands call the Admin REST API — NOT direct AWS SDK calls.
Requires operator to be logged in via Entra (make ops-login).

Usage:
    uv run python scripts/ops.py <command> [options]

Commands:
    login                   Authenticate as operator via Entra
    top-tenants             List top N tenants by token consumption
    tenant-sessions         Show active sessions for a tenant
    suspend-tenant          Suspend a tenant immediately
    reinstate-tenant        Reinstate a suspended tenant
    quota-report            Show AgentCore quota utilisation
    invocation-report       Show invocation report for a tenant
    security-events         Show tenant access violation events
    dlq-inspect             Inspect messages in a DLQ
    dlq-redrive             Redrive messages from DLQ to main queue
    error-rate              Show error rate for last N minutes
    failover-lock-acquire   Acquire distributed lock before region failover
    failover-lock-release   Release distributed lock after region failover
    set-runtime-region      Update active runtime region (requires lock)
    notify-tenant           Send notification to tenant owner
    service-health          Check AWS service health for AgentCore regions
    billing-status          Check billing Lambda status and last run
    update-tenant-budget    Update a tenant's monthly budget
    fail-job                Manually mark an async job as failed
    audit-export            Export audit trail for a tenant
    page-security           Page the security team

Implemented in TASK-029.
"""
