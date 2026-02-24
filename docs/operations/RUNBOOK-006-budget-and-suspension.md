# RUNBOOK-006: Tenant Budget Alert and Suspension

## Trigger (80% alert)
- SNS notification: platform.tenant.budget_warning
- Tenant has consumed 80% of their monthlyBudgetUsd

## Trigger (100% — automatic suspension)
- Billing Lambda automatically sets tenant status=suspended
- EventBridge event: platform.tenant.budget_exceeded

## Actions on 80% Warning

### 1. Investigate if spike is expected
```bash
make ops-invocation-report TENANT={tenantId} DAYS=7 ENV=prod
# Shows: daily token consumption, agent breakdown, cost estimate
```

### 2. Notify tenant owner
```bash
make ops-notify-tenant TENANT={tenantId} TEMPLATE=budget_warning_80pct ENV=prod
# Sends email to ownerEmail on the TENANT record
```

### 3. If runaway agent (spike not expected)
```bash
# Suspend immediately — saves the tenant money and platform quota
make ops-suspend-tenant TENANT={tenantId} REASON="runaway_agent_budget_protection" ENV=prod
# Investigate with tenant before reinstating
```

### 4. If legitimate growth
- Advise tenant to increase their monthlyBudgetUsd via Platform.Admin:
  make ops-update-tenant-budget TENANT={tenantId} BUDGET={new_value} ENV=prod
- Or upgrade tier (higher tier = higher default budget)

## Actions on 100% (already suspended automatically)

### 1. Verify suspension
```bash
make ops-tenant-sessions TENANT={tenantId} ENV=prod
# Should show: status=suspended, no active sessions
```

### 2. Notify tenant
```bash
make ops-notify-tenant TENANT={tenantId} TEMPLATE=budget_exceeded_suspended ENV=prod
```

### 3. Reinstate options
- Tenant increases budget: make ops-update-tenant-budget + make ops-reinstate-tenant
- Tenant upgrades tier: tier change event triggers budget reset
- Do NOT reinstate without budget increase or tier upgrade

## Billing Lambda Failure (FM-10)
If billing Lambda has failed (DLQ alarm on billing Lambda DLQ):
- Billing is eventually consistent — this does NOT suspend tenants immediately
- Fix billing Lambda, redrive DLQ (RUNBOOK-005)
- Billing catches up; suspension may fire retroactively
- Alert ops when caught up: make ops-billing-status ENV=prod
