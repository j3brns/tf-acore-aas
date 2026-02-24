# RUNBOOK-003: Tenant Access Violation

## Trigger
- CloudWatch alarm: platform-tenant-access-violation (any event)
- CloudWatch metric: platform.security.tenant_access_violation > 0

## Severity: HIGH — potential data breach — treat as security incident

## IMPORTANT: Do not dismiss this alarm without investigation. Every violation
## must be reviewed within 2 hours.

## Immediate Actions

### 1. Get violation details immediately
```bash
make ops-security-events ENV=prod HOURS=1
# Shows: tenantId, callerTenantId, attempted key, requestId, Lambda function, timestamp
```

### 2. Determine source
The output will show which Lambda function raised TenantAccessViolation.
Two possible causes:
- **Internal Lambda bug**: the violation is in platform code (data-access-lib not used)
- **External malicious caller**: a tenant is attempting to access another tenant's data

### 3. If external caller (malicious attempt)
```bash
# Suspend immediately pending investigation
make ops-suspend-tenant TENANT={callerTenantId} REASON="security_investigation" ENV=prod
# Page security team
make ops-page-security INCIDENT="tenant_access_violation" TENANT={tenantId} ENV=prod
```

### 4. If internal Lambda bug
```bash
# Identify the Lambda and the code path
make logs-{lambda-name} ENV=prod MINUTES=30 | grep TenantAccessViolation
# Does not require tenant suspension — this is a platform bug
# File P1 bug immediately, assign to platform team
# Notify affected tenant if their data was potentially accessible
```

### 5. Evidence preservation
```bash
# Export CloudTrail events for the time window
make ops-audit-export TENANT={tenantId} START={timestamp-5min} END={timestamp+5min} ENV=prod
# Do not modify any DynamoDB records until investigation complete
```

## Post-Incident
- Root cause analysis within 48 hours
- If external: report under GDPR Article 33 (72-hour notification to ICO) if breach confirmed
- If internal bug: fix and deploy, add regression test, post-mortem
