# CLAUDE.md — Rules for AI Coding Assistants
# Read this at the start of every session. No exceptions.

## What This Platform Is

A production multi-tenant Agent as a Service platform on Amazon Bedrock AgentCore.
B2B tenants invoke AI agents via REST API. The platform manages isolation, identity,
memory, tool access, billing, and observability. This is a production system — not
a prototype — with real tenants, real data, and real compliance obligations.

## Priority Order

When trade-offs arise, resolve in this order:
1. Security — a security flaw ships last, regardless of schedule
2. Operability — ops must run this at 3am without a developer on call
3. Correctness — wrong behaviour is worse than slow behaviour
4. Performance — optimise only after correctness is proven
5. Developer experience — the inner loop matters, but it is last

## Absolute Constraints (non-negotiable)

If any implementation path violates these, stop, state the conflict, propose an
alternative. Never silently work around them.

1. No Cognito anywhere. Auth is Entra ID OIDC/JWT for humans, SigV4 for machines.
2. No hardcoded credentials, ARNs, account IDs, secrets, or region strings.
3. No IAM policies with wildcard Action or wildcard Resource.
4. No public S3 buckets.
5. No long-lived AWS access keys. Bootstrap IAM user deleted after first deploy.
6. No secrets in GitLab CI/CD variables — Secrets Manager only.
7. Every Lambda: X-Ray tracing, DLQ, structured JSON logging with appid+tenantid.
8. Every DynamoDB table: PITR, KMS encryption, deletion protection in staging/prod.
9. AgentCore Runtime is arm64 only. Dependencies cross-compiled aarch64-manylinux2014.
   Sync limit 15 minutes. Async uses app.add_async_task / app.complete_async_task.
10. No impersonation — act-on-behalf only. Original JWT never reaches tool Lambdas.
11. appid and tenantid on every log line, metric dimension, and trace annotation.
12. data-access-lib is the only permitted way to access DynamoDB from Lambda handlers.
13. No superuser IAM roles in normal operation.
14. All data remains in the EU at all times.

## How To Work

Before writing any code:
1. Read this file
2. Read docs/ARCHITECTURE.md
3. Read the ADR(s) linked to the current task in docs/TASKS.md
4. Run `make validate-local` — confirm it passes
5. State which task you are working on explicitly

Before marking any task complete:
1. All tests pass
2. `make validate-local` passes
3. New infrastructure passes cfn-guard
4. State "TASK-NNN complete. Tests passing."

When uncertain about a security decision — stop and ask. Do not guess.

## When To Stop And Ask

- Any change to DynamoDB partition key or GSI design
- Any change to IAM policies or trust relationships
- Any change to authoriser Lambda validation logic
- Any new dependency adding >10MB to the deployment package
- Any change affecting tenant isolation in data-access-lib
- Any change to KMS key policy
- Any operation touching production data

## Naming Conventions

- AWS resources: platform-{resource}-{environment}
- Python: snake_case everywhere
- TypeScript: camelCase properties, PascalCase classes
- Environment variables: SCREAMING_SNAKE_CASE
- SSM: /platform/{category}/{name}
- DynamoDB keys: {ENTITY}#{id}

## Forbidden Patterns

```python
# FORBIDDEN: raw boto3 DynamoDB in handlers
dynamodb.Table('platform-tenants').get_item(...)

# REQUIRED: data-access-lib only
from data_access import TenantScopedDynamoDB
db = TenantScopedDynamoDB(tenant_context)

# FORBIDDEN: hardcoded region
boto3.client('ssm', region_name='eu-west-2')

# REQUIRED: from environment
boto3.client('ssm', region_name=os.environ['AWS_REGION'])

# FORBIDDEN: bare exception silencing
try:
    do_something()
except Exception:
    pass

# REQUIRED: log and handle
try:
    do_something()
except TenantAccessViolation as e:
    logger.error("Tenant access violation", extra={"tenant_id": tenant_id})
    return error_response(403, "UNAUTHORISED")
```

## Technology Stack

| Concern            | Technology                      |
|--------------------|---------------------------------|
| Agent runtime      | AgentCore Runtime eu-west-1     |
| Human auth         | Microsoft Entra ID OIDC         |
| Machine auth       | AWS SigV4                       |
| IaC platform       | CDK TypeScript strict           |
| IaC account vend   | Terraform HCL                   |
| Python packaging   | uv + pyproject.toml             |
| Logging            | aws_lambda_powertools Logger    |
| Testing CDK        | Jest + cdk assertions           |
| Testing Python     | pytest + LocalStack             |
| Secrets            | AWS Secrets Manager             |
| Config             | SSM Parameter Store             |
| Async agents       | AgentCore add_async_task SDK    |
| Observability      | AgentCore Observability + CW    |
