# RUNBOOK-004: AgentCore Quota Increase Request

## Trigger
- RUNBOOK-002 identifies utilisation approaching 80%

## Process

### 1. Gather current usage data
```bash
make ops-quota-report ENV=prod
# Note: current limit, current peak, growth rate (last 30 days)
```

### 2. Calculate required headroom
Target: 50% utilisation at expected peak (not current peak).
If current peak is 60 concurrent sessions at 80% of limit (75 sessions):
Required headroom = current_peak / 0.5 = 120 concurrent sessions minimum.
Add 20% buffer: request 150 concurrent sessions.

### 3. Submit AWS Support request
1. Log in to AWS Support Console
2. Create case: Service limit increase
3. Service: Amazon Bedrock AgentCore
4. Quota: InvokeAgentRuntime requests per second (and ConcurrentSessions)
5. Region: eu-west-1 (Dublin) — this is where Runtime lives
6. New limit: calculated value from step 2
7. Use case: "Production multi-tenant agent platform, organic growth"
8. Attach quota-report output

### 4. Typical response time
3–5 business days for standard requests. Escalate via TAM if urgent.

### 5. While waiting
If utilisation reaches 85% before quota increase is approved:
- Activate soft throttle: pause new basic-tier tenant onboarding
- Do NOT pause standard or premium tenants
- Notify affected basic tenants with ETA

## Quota Increase Escalation Path
If 3 business days pass without response: escalate via AWS TAM or account manager.
If utilisation reaches 90% before approval: activate Option B account split (RUNBOOK-002).
