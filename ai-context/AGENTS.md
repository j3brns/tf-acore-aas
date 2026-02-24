# AI Coding Assistant Context

## Read First
Always read CLAUDE.md at the project root before starting any task.
Always read docs/ARCHITECTURE.md before changing infrastructure.
Always read the relevant ADR in docs/decisions/ before reversing a decision.
Run `make plan-dev TASK="description"` before implementing anything non-trivial.

## Task State
Current task list is in docs/TASKS.md.
Each task has a status: [ ] not started, [~] in progress, [x] done, [!] blocked.
State the task number when you start: "Starting TASK-016: authoriser Lambda"

## Key Principles
- This is a multi-tenant platform. EVERY operation must be tenant-scoped.
- appid and tenantid must appear on every log line, metric, and trace.
- data-access-lib (src/data-access-lib/) is the ONLY permitted DynamoDB interface.
  Never write raw boto3 DynamoDB calls in Lambda handlers.
- AgentCore Runtime is arm64 only. All Python deps need arm64 cross-compilation.
- Authentication: Entra JWT for humans, SigV4 for machines. No Cognito.
- Invocation mode is DECLARED (sync|streaming|async), never inferred at runtime.
- Async mode uses app.add_async_task / app.complete_async_task — NOT SQS routing.

## When To Stop And Ask
- Any change to DynamoDB partition key or GSI design
- Any change to IAM policies or trust relationships
- Any change to authoriser Lambda validation logic
- Any new dependency adding >10MB to the deployment package
- Any change affecting tenant isolation in data-access-lib
- Any change to KMS key policy
- Any operation touching production data

## Forbidden Patterns
See CLAUDE.md — Forbidden Patterns section.

## Architecture Decisions
ADR-001: AgentCore Runtime chosen over custom orchestration
ADR-002: Entra ID not Cognito
ADR-003: REST API not HTTP API
ADR-004: Act-on-behalf not impersonation
ADR-005: Declared invocation mode not runtime detection
ADR-006: uv and pyproject.toml
ADR-007: CDK + Terraform (not pure Terraform)
ADR-008: ZIP deployment default
ADR-009: eu-west-2 home, eu-west-1 Runtime
ADR-010: AgentCore native async not SQS routing
ADR-011: Thin BFF only
ADR-012: On-demand vs provisioned DynamoDB
ADR-013: Entra group-to-role claim for RBAC
