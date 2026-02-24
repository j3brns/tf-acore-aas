# RUNBOOK-001: Runtime Region Failover

## Trigger
- CloudWatch alarm: platform-runtime-region-failover fires
- Bridge Lambda logs: ServiceUnavailableException from eu-west-1
- Error rate >5% with runtime_region=eu-west-1 in bridge Lambda logs

## Severity: HIGH — active customer impact

## Immediate Actions (target: <5 minutes to failover)

### 1. Verify the outage
```bash
# Check AWS Service Health for eu-west-1 AgentCore
make ops-service-health ENV=prod
# If confirmed: proceed. If uncertain: wait 2 minutes, re-check.
```

### 2. Acquire distributed lock
```bash
make failover-lock-acquire ENV=prod
# IMPORTANT: must succeed before touching SSM
# If lock already held: another operator is acting — coordinate before proceeding
# Expected output: Lock acquired: platform-runtime-failover
```

### 3. Switch runtime region
```bash
make infra-set-runtime-region REGION=eu-central-1 ENV=prod
# Updates SSM /platform/config/runtime-region
# Bridge Lambda caches this for 60s — allow 90s for all instances to pick up
# Expected output: Runtime region updated to eu-central-1
```

### 4. Verify traffic routing
```bash
make logs-bridge ENV=prod MINUTES=5 | grep runtimeRegion
# Should show: "runtimeRegion":"eu-central-1" on new invocations
# Expected output: eu-central-1 visible within 90 seconds
```

### 5. Monitor error rate
```bash
make ops-error-rate ENV=prod MINUTES=5
# Should drop below 1% within 2 minutes
# If not: check AgentCore status in Frankfurt
```

### 6. Release lock
```bash
make failover-lock-release ENV=prod
# Expected output: Lock released
```

## Recovery (when Dublin is restored)

### 1. Confirm Dublin recovery
```bash
make ops-service-health ENV=prod
# Confirm eu-west-1 AgentCore status: Operational
```

### 2. Acquire lock, switch back, release
```bash
make failover-lock-acquire ENV=prod
make infra-set-runtime-region REGION=eu-west-1 ENV=prod
# Wait 90 seconds, verify logs
make failover-lock-release ENV=prod
```

## Post-Incident
- File incident report within 24 hours
- Check Frankfurt quota headroom after failover (higher latency = longer sessions = more quota)
- Confirm all DLQ messages from during failover have been processed:
  make ops-dlq-inspect QUEUE=platform-async-dlq-prod ENV=prod

## Notes
- Frankfurt adds ~25ms RTT vs Dublin's ~12ms — visible in P99 latency metrics
- If Frankfurt is also unavailable: platform is degraded, no further failover option
- Data remains in eu-west-2 throughout — only compute changes
