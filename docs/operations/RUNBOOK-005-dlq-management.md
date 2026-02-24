# RUNBOOK-005: DLQ Management

## Trigger
- CloudWatch alarm: any DLQ depth > 0

## DLQ Inventory
| DLQ Name                            | Source Lambda         | Max receive count |
|-------------------------------------|-----------------------|-------------------|
| platform-bridge-dlq-{env}           | bridge                | 3                 |
| platform-authoriser-dlq-{env}       | authoriser            | 3                 |
| platform-tenant-api-dlq-{env}       | tenant-api            | 3                 |
| platform-interceptor-req-dlq-{env}  | request-interceptor   | 3                 |
| platform-webhook-dlq-{env}          | webhook-delivery      | 3                 |

## Immediate Actions

### 1. Inspect the DLQ
```bash
make ops-dlq-inspect QUEUE=platform-bridge-dlq-prod ENV=prod
# Shows: message body, error reason, receive count, first received timestamp
```

### 2. Diagnose root cause from message content
Common causes:
- Bridge DLQ: Runtime unavailable (check RUNBOOK-001), tenant role assumption failed
- Authoriser DLQ: Secrets Manager throttling (check /tmp cache is working), Entra JWKS unreachable
- Interceptor DLQ: Idempotency table issue, JWKS fetch failure
- Webhook DLQ: Tenant webhook URL unreachable (4xx or 5xx), network issue

### 3. Fix the root cause first
Do NOT redrive until root cause is fixed. Redriving into a broken system just puts
messages back in the DLQ.

### 4. Redrive after fix
```bash
make ops-dlq-redrive QUEUE=platform-bridge-dlq-prod ENV=prod
# Moves messages from DLQ back to the main queue
# Lambda will retry up to max receive count again
```

### 5. Monitor after redrive
```bash
make ops-error-rate ENV=prod MINUTES=10
# Confirm error rate is recovering
```

## Async Job DLQ (platform-async-dlq-{env})
Messages here are jobs where the agent failed 3 times.
Each message has a jobId. Update the JOB record manually after investigation:
```bash
make ops-fail-job JOB={jobId} REASON="agent_error_after_retries" ENV=prod
# This marks the job as failed and notifies the tenant if webhook registered
```
SLA: async jobs must complete or fail within 8 hours of submission.
