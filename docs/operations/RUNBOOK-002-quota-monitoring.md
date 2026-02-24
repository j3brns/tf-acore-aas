# RUNBOOK-002: AgentCore Quota Monitoring

## Trigger
- CloudWatch alarm: platform-quota-utilisation-high (ConcurrentSessions > 70% of limit)

## Severity: MEDIUM — approaching capacity, no current customer impact

## Immediate Actions

### 1. Get current quota picture
```bash
make ops-quota-report ENV=prod
# Shows: current concurrent sessions, account limit, utilisation %
# Also shows: top 10 tenants by active session count
```

### 2. Identify if a single tenant is dominant
```bash
make ops-top-tenants ENV=prod N=10
# If one tenant has >40% of sessions: likely a runaway agent
# Check for runaway:
make ops-tenant-sessions TENANT={tenantId} ENV=prod
```

### 3a. If runaway agent detected
```bash
make ops-suspend-tenant TENANT={tenantId} REASON="quota_protection_runaway_agent" ENV=prod
# Notify tenant owner via:
make ops-notify-tenant TENANT={tenantId} TEMPLATE=runaway_agent_suspended ENV=prod
# Investigate root cause before reinstating
```

### 3b. If organic growth (no runaway)
- Check growth trend: if growing >10% per week, file quota increase request now
- Do not wait for 90% before filing

### 4. File quota increase request (if needed)
See RUNBOOK-004 for the request process.

## Quota Thresholds and Actions

| Utilisation | Action                                                |
|-------------|-------------------------------------------------------|
| 70%         | This runbook — investigate and plan                   |
| 80%         | File quota increase request now, prepare Option B     |
| 90%         | Pause new basic/standard tenant onboarding            |
| 95%         | Emergency: activate Option B account split            |

## Option B Account Split (at 80% utilisation)
See docs/ARCHITECTURE.md "Scaling Model" for topology description.
Requires TASK-049 (account vending Terraform) to be complete.
