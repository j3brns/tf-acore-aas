# TASKS.md — Atomic Task List for Claude Code Sessions

## How To Use This File

Each task is a single Claude Code session. Before starting any task:
1. Read CLAUDE.md
2. Read docs/ARCHITECTURE.md
3. Read the ADR(s) listed for that task
4. Run `make validate-local` and confirm it passes
5. State explicitly: "Starting TASK-NNN: [title]"

Before marking any task complete:
1. All tests for the task must pass
2. `make validate-local` must pass
3. New infrastructure must pass cfn-guard
4. State explicitly: "TASK-NNN complete. Tests passing."

**Do not combine tasks.** One task per session. A task that is too large to complete
in one session should be split — raise this before starting, not after.

## Status Key
- `[ ]` Not started
- `[~]` In progress (add: who, date started)
- `[x]` Done (add: date completed, commit SHA)
- `[!]` Blocked (add: what is blocking it)

---

## Phase 0 — Foundation (no code, decisions only)

[x] TASK-001  Write CLAUDE.md
              Constraints, priority order, naming conventions, forbidden patterns
              ADRs: none | Tests: none | Gate: operator review
              Done: 2026-02-24

[x] TASK-002  Write docs/ARCHITECTURE.md
              Region topology, request lifecycle, data model, scaling, failure modes
              ADRs: none | Tests: none | Gate: operator review
              Done: 2026-02-24

[x] TASK-003  Write ADR-001 through ADR-013
              See docs/decisions/ — one file per ADR
              ADRs: none (these ARE the ADRs) | Tests: none
              Done: 2026-02-24

[x] TASK-004  Write docs/operations/ runbooks
              RUNBOOK-000 through RUNBOOK-009
              ADRs: none | Tests: none | Gate: operator review
              Done: 2026-02-24

[x] TASK-005  Write docs/PLAN.md, docs/TASKS.md (this file), docs/ROADMAP.md
              ADRs: none | Tests: none
              Done: 2026-02-24

[x] TASK-006  Write docs/bootstrap-guide.md and docs/entra-setup.md
              Prerequisites, ordered bootstrap steps, Entra app registration
              ADRs: ADR-002 | Tests: none
              Done: 2026-02-24
              Note: Fixed --start-at/--step flag inconsistency (D-003)

[x] TASK-007  Write docs/security/THREAT-MODEL.md
              Threat actors, attack surfaces, mitigations
              ADRs: ADR-004 | Tests: none
              Done: 2026-02-24

[x] TASK-008  Write docs/security/COMPLIANCE-CHECKLIST.md
              GDPR, UK ICO, SOC2 controls mapped to implementation
              ADRs: none | Tests: none
              Done: 2026-02-24

[x] TASK-009  Write docs/development/AGENT-DEVELOPER-GUIDE.md
              Full guide for internal agent developers
              ADRs: ADR-005, ADR-006, ADR-008 | Tests: none
              Done: 2026-02-24

[x] TASK-010  Create directory skeleton and Makefile stubs
              src/ gateway/ scripts/ agents/ infra/ spa/ tests/ with stub files
              Makefile fully stubbed and validate-local passes
              ADRs: none | Tests: make validate-local passes
              Done: 2026-02-24
              Note: Lambda src dirs use snake_case (snake_case everywhere per CLAUDE.md)
              Note: Fixed Makefile infra-set-runtime-region to use $$AWS_REGION (D-002)
              Note: Fixed logs-* MINUTES parameter (O-001)
              Note: Added scripts/task.py + make task-{next,list,start,resume,finish,prompt}
                    task-start auto-selects next [ ] task; marks [~] in worktree on start
                    task-resume auto-selects first [~] task with existing worktree
                    Task Workflow (Worktree Protocol) section added to CLAUDE.md

**Phase 0 Gate**: Platform engineer and operator both sign off all docs before Phase 1.

---

## Phase 1 — Data Contracts (no infra, no Lambda)

[x] TASK-011  Define all DynamoDB table schemas as Python dataclasses
              File: src/data-access-lib/src/data_access/models.py
              Tables: tenants, agents, invocations, jobs, sessions, tools, ops-locks
              PRESENT FOR REVIEW before writing any Lambda code
              ADRs: ADR-012 | Tests: pytest validate schema constraints
              Gate: schema reviewed and confirmed before Phase 2
              Done: 2026-02-24, commit bf9c8dd
              58 tests passing. validate-local passes.

[x] TASK-012  Write OpenAPI 3.1 specification
              File: docs/openapi.yaml
              All routes, request schemas, response schemas, error codes
              Include: /v1/agents, /v1/tenants, /v1/jobs, /v1/webhooks, /v1/bff, /v1/health
              PRESENT FOR REVIEW before implementing any routes
              ADRs: ADR-003 | Tests: spectral lint must pass
              Gate: spec reviewed and confirmed before Phase 2
              Done: 2026-02-25, commit b19986c

[x] TASK-013  Write data-access-lib
              Files: src/data-access-lib/src/data_access/
              TenantScopedDynamoDB — enforces tenant partition on every operation
              TenantScopedS3 — enforces tenant prefix on every operation
              TenantAccessViolation exception
              ADRs: ADR-012 | Tests: 100% coverage required (security-critical)
              Coverage assertion: cross-tenant read raises TenantAccessViolation
              Coverage assertion: cross-tenant write raises TenantAccessViolation
              Coverage assertion: TenantAccessViolation emits CloudWatch metric
              Done: 2026-02-25, commit 911b829
              54 tests passing. 100% coverage (296 statements). validate-local passes.

**Phase 1 Gate**: Data models and OpenAPI spec reviewed. Schemas confirmed.
Nothing in Phase 2 starts until written confirmation.

---

## Phase 2 — Local Development Loop

[x] TASK-014  Write docker-compose.yml
              Services: LocalStack, mock AgentCore Runtime, mock JWKS endpoint
              Mock Runtime: FastAPI on :8765, POST /invocations, GET /ping
              Returns canned streaming response. Logs tenant context headers.
              Mock JWKS: FastAPI on :8766, issues test JWTs, serves /.well-known/jwks.json
              ADRs: none | Tests: make dev must start cleanly
              Done: 2026-02-25, commit TBD

[ ] TASK-015  Write scripts/dev-bootstrap.py
              Seeds two test tenants (basic-tier, premium-tier)
              Seeds all SSM parameters pointing to LocalStack
              Seeds DynamoDB tables with fixtures
              Writes test JWTs to .env.test
              Idempotent — safe to run multiple times
              ADRs: none | Tests: run twice, verify no duplicate records

[ ] TASK-016  Write src/authoriser/handler.py
              Entra JWT path: JWKS fetch+cache, sig validate, expiry, audience, issuer
              Roles claim check for admin routes (Platform.Admin, Platform.Operator)
              SigV4 path: signature validate, x-tenant-id header
              Tenant status check: DynamoDB confirm status=active
              Returns usageIdentifierKey for usage plan enforcement
              ADRs: ADR-002, ADR-004 | Tests: >80% coverage
              Test cases: valid JWT, expired, wrong audience, wrong issuer,
              cross-tenant header injection, suspended tenant, admin route non-admin JWT

[ ] TASK-017  Write src/tenant-api/handler.py
              CREATE: validate, conditional write, provision Memory store,
              create API key in Secrets Manager, publish EventBridge event
              READ: authorise (own tenant or Platform.Admin), enrich with usage
              UPDATE: admin only, tier/budget/status, publish tier_changed event
              DELETE: soft delete, 30-day retention, EventBridge event
              Uses data-access-lib exclusively — no raw DynamoDB calls
              ADRs: ADR-012 | Tests: CRUD + isolation + soft delete + event emission

[ ] TASK-018  Write src/bridge/handler.py
              Reads invocation_mode from agent registry
              sync: invoke Runtime, wait up to 15min, write INVOCATION record
              streaming: SSE relay via Lambda response streaming
              async: write JOB record, invoke Runtime with add_async_task context, 202
              Region failover via SSM (cached 60s) with DynamoDB distributed lock
              Assumes tenant execution role via STS
              ADRs: ADR-005, ADR-009, ADR-010 | Tests: all three modes mocked

[ ] TASK-019  Implement full Makefile
              All targets from Makefile skeleton now actually work
              make dev, make dev-stop, make test-unit, make test-int
              make validate-local, make bootstrap, make worktree-*
              make agent-push, make agent-invoke, make agent-test
              make ops-*, make logs-*, make spa-*
              ADRs: none | Tests: make validate-local must pass end-to-end

[ ] TASK-020  Write agents/echo-agent/ reference pattern
              Demonstrates all three invocation modes (sync, streaming, async)
              async mode uses app.add_async_task / app.complete_async_task
              Full test suite, golden test cases (3 per mode)
              End-to-end through full local stack
              ADRs: ADR-005, ADR-008 | Tests: all golden cases pass

**Phase 2 Gate**: make dev + make test-unit both pass clean.
Echo agent invocable end-to-end in local environment in all three modes.

---

## Phase 3 — CDK Infrastructure

[ ] TASK-021  NetworkStack
              VPC, private/public subnets, VPC endpoints (S3, DynamoDB, SSM,
              Secrets Manager, AgentCore), security groups, NACLs
              VPC peering or PrivateLink to eu-west-1 for Runtime invocation
              ADRs: ADR-009 | Tests: Jest construct tests

[ ] TASK-022  IdentityStack
              GitLab OIDC WIF provider + pipeline roles (one per stage, least-privilege)
              Entra JWKS Lambda layer (bakes JWKS URL into layer)
              KMS keys: one per data classification (tenant-data, platform-config, logs)
              KMS key policies: no wildcard principal
              ADRs: ADR-002 | Tests: Jest construct tests

[ ] TASK-023  PlatformStack
              REST API (not HTTP API): usage plans, per-method throttle, WAF association
              WAF: AWS managed rules + UK IP rate limiting + custom rules
              CloudFront distribution: S3 OAC, CSP response headers policy
              Bridge Lambda, BFF Lambda, Authoriser Lambda (provisioned concurrency=10)
              AgentCore Gateway resource with REQUEST+RESPONSE interceptors wired
              ADRs: ADR-003, ADR-004, ADR-011 | Tests: Jest construct tests

[ ] TASK-024  AgentCoreStack
              Runtime configuration pointing to eu-west-1
              Memory template (provisioned per-tenant in TenantStack)
              Identity configuration for Entra JWKS
              Observability metric stream eu-west-1→eu-west-2
              ADRs: ADR-001, ADR-009 | Tests: Jest construct tests

[ ] TASK-025  TenantStack
              Provisioned per-tenant by EventBridge trigger on platform.tenant.created
              Creates: Memory store, execution role (scoped to tenant S3/DynamoDB),
              usage plan API key, SSM parameters for tenant
              CDK context input: tenantId, tier, accountId
              ADRs: ADR-012 | Tests: Jest construct tests

[ ] TASK-026  ObservabilityStack
              Per-tenant CloudWatch dashboard (provisioned in TenantStack)
              Platform operations dashboard
              All 10 FM alarms (see ARCHITECTURE.md failure modes table)
              Budget alarm per tenant against monthlyBudgetUsd
              Metric streams AgentCore Observability eu-west-1 → CloudWatch eu-west-2
              ADRs: none | Tests: Jest construct tests

[ ] TASK-027  cfn-guard rules
              File: infra/cdk/guard/platform-security.guard
              Rules: no wildcard IAM, no public S3, PITR on DynamoDB, KMS encryption,
              deletion protection, VPC for Lambdas, DLQ configured, X-Ray enabled
              ADRs: none | Tests: cfn-guard validate against synthesised templates

**Phase 3 Gate**: cdk synth passes. cfn-guard passes. infra-diff reviewed by operator.
make bootstrap-dev succeeds. Operator completes RUNBOOK-001 in dev.

---

## Phase 4 — Bootstrap and Operations Tooling

[ ] TASK-028  Write scripts/bootstrap.py
              Ordered steps: CDK bootstrap (all 3 regions), secrets seeding,
              GitLab OIDC wiring, first CDK deploy, post-deploy seeding,
              smoke test (invoke echo-agent), delete bootstrap IAM user
              Validates each step before proceeding to next
              Writes bootstrap-report.json to S3 (audit trail)
              ADRs: ADR-007 | Tests: run in dev, verify bootstrap-report.json

[ ] TASK-029  Write scripts/ops.py
              Full operations CLI. All commands call Admin REST API — not direct AWS SDK.
              Commands: top-tenants, tenant-sessions, suspend-tenant, reinstate-tenant,
              quota-report, invocation-report, security-events, dlq-inspect, dlq-redrive,
              error-rate, failover-lock-acquire, failover-lock-release,
              set-runtime-region, notify-tenant
              ADRs: none | Tests: unit tests with mocked REST API responses

[ ] TASK-030  Write scripts/failover_lock.py
              DynamoDB conditional write for lock acquire (prevents race condition)
              TTL 5-minute auto-expire on lock record
              Release on success or error (finally block)
              Used by: infra-set-runtime-region Makefile target
              ADRs: ADR-009 | Tests: concurrent acquire, only one succeeds

[ ] TASK-031  Implement Admin REST API routes
              POST /v1/platform/failover
              GET  /v1/platform/quota
              GET  /v1/tenants (list all, Platform.Admin only)
              GET  /v1/tenants/{id}/audit-export
              POST /v1/platform/quota/split-accounts
              ADRs: ADR-002 | Tests: role enforcement, non-admin gets 403

[ ] TASK-032  Test all runbooks in dev environment
              Execute every runbook scenario with ops.py and make targets
              Document any steps that fail, fix the tooling
              RUNBOOK-001: region failover end-to-end
              RUNBOOK-002: tenant quota monitoring
              RUNBOOK-003: tenant access violation detection
              RUNBOOK-004: quota increase request (simulate, no actual AWS support ticket)
              RUNBOOK-005: DLQ drain and redrive
              RUNBOOK-006: budget alert and suspension
              RUNBOOK-007: deployment rollback
              RUNBOOK-008: developer onboarding (new person follows guide to working state)
              RUNBOOK-009: operator onboarding
              ADRs: none | Tests: all runbooks complete without console access

**Phase 4 Gate**: Operator completes every runbook in dev using make targets only.
No AWS console access permitted during runbook testing.

---

## Phase 5 — Agent Developer Experience

[ ] TASK-033  Write scripts/hash_layer.py
              Read [project.dependencies] from agent pyproject.toml
              Canonical serialisation: sorted keys, no whitespace variance
              SHA256, return first 16 hex chars
              Compare to SSM /platform/layers/{agentName}/hash
              Exit 0 = match (fast path), Exit 1 = mismatch (rebuild)
              ADRs: ADR-006, ADR-008 | Tests: same deps = same hash, order invariant

[ ] TASK-034  Write scripts/build_layer.py
              uv pip install --python-platform aarch64-manylinux2014 --python-version 3.12
              --target=.build/deps --only-binary=:all:
              Zip .build/deps/ to .build/{agent}-deps-{hash}.zip
              Upload to S3, update SSM hash and s3-key
              ADRs: ADR-006 | Tests: verify arm64 binary format in zip

[ ] TASK-035  Write scripts/package_agent.py, deploy_agent.py, register_agent.py
              package: zip agent code excluding pycache, .venv, tests
              deploy: invoke AgentCore Runtime create/update API
              For zip mode: code_zip with s3_bucket, deps_key, script_key
              For container mode: buildx --platform linux/arm64, push ECR
              register: write to DynamoDB platform-agents, SSM runtime ARN
              ADRs: ADR-005, ADR-008 | Tests: dry run mode verified

[ ] TASK-036  Write gateway/interceptors/request_interceptor.py
              Validate Bearer JWT against Entra JWKS
              Check tierMinimum for requested tool
              Return 403 immediately if tier insufficient (tool never invoked)
              Issue scoped act-on-behalf token for the specific tool
              Inject x-tenant-id, x-app-id, x-tier, x-acting-sub headers
              Idempotency: Lambda Powertools keyed on Mcp-Session-Id + body.id
              ADRs: ADR-004 | Tests: tier enforcement, scoped token structure

[ ] TASK-037  Write gateway/interceptors/response_interceptor.py
              tools/list: filter to tierMinimum <= tenant tier
              tools/call: PII scan and redact (UK NI, NHS, sort code, account, email)
              PII patterns from SSM /platform/gateway/pii-patterns/default
              ADRs: ADR-004 | Tests: PII redaction, tier filtering, passthrough for allowed

[ ] TASK-038  Write src/bff/handler.py
              POST /v1/bff/token-refresh: Entra OBO flow, returns new scoped token
              POST /v1/bff/session-keepalive: fire-and-forget ping to Runtime session
              Prevents 15-minute idle timeout destroying active streaming sessions
              ADRs: ADR-011 | Tests: keepalive ping verified against mock Runtime

[ ] TASK-039  Write .gitlab-ci-agent.yml
              Triggered on changes to agents/**
              Stages: validate → test → push-dev → promote-staging → promote-prod
              validate: ruff, pyproject.toml schema check, detect-secrets
              test: pytest unit + golden tests against mock Runtime
              push-dev: make agent-push AGENT=... ENV=dev (auto on branch push)
              promote-staging: manual gate, runs AgentCore Evaluations gate
              promote-prod: two-reviewer approval
              ADRs: ADR-005 | Tests: pipeline lint, dry run

**Phase 5 Gate**: make agent-push AGENT=echo-agent ENV=dev completes <30s warm path.
All three invocation modes work end-to-end in dev AWS environment.

---

## Phase 6 — SPA Frontend

[ ] TASK-040  Scaffold SPA
              Vite + React + TypeScript + Tailwind CSS + shadcn/ui
              Directory: spa/
              MSAL.js (@azure/msal-browser) configuration
              All values from VITE_ environment variables — none hardcoded
              ADRs: ADR-002 | Tests: npm run build passes

[ ] TASK-041  MSAL auth layer and API client
              spa/src/auth/: msalConfig.ts, AuthProvider.tsx, useAuth.ts
              spa/src/api/client.ts: fetch wrapper, Bearer injection, 401 refresh
              Token refresh via acquireTokenSilent then acquireTokenPopup fallback
              Fetch + ReadableStream for streaming (not EventSource — no auth header)
              ADRs: ADR-002, ADR-003, ADR-011 | Tests: auth flow mocked

[ ] TASK-042  Agent catalogue, invoke, sessions, admin pages
              AgentCataloguePage, InvokePage (all three modes), SessionsPage
              AdminPage: platform health, requires Platform.Operator role claim
              JobStatus polling component for async invocations
              ADRs: none | Tests: component tests

[ ] TASK-043  CloudFront CSP and CORS
              Response headers policy: full CSP, X-Frame-Options, HSTS
              REST API CORS: AllowOrigins from CloudFront domain only (not wildcard)
              ADRs: ADR-003 | Tests: CORS preflight passes, CSP headers present

**Phase 6 Gate**: Operator logs in via Entra, invokes echo-agent in all three modes,
sees results. Admin view shows platform health metrics.

---

## Phase 7 — CI/CD Pipeline

[ ] TASK-044  Write .gitlab-ci.yml platform pipeline
              Stages: validate → test → plan → deploy-dev → deploy-staging → deploy-prod
              validate: ruff, mypy, tsc, cdk synth, cfn-guard, detect-secrets
              test: Jest CDK tests + pytest unit + pytest integration
              plan: cdk diff posted as MR comment
              deploy-dev: auto on merge to main
              deploy-staging: manual gate, canary 10% for 30 minutes
              deploy-prod: two-reviewer approval, canary, auto-rollback
              All stages use GitLab WIF OIDC — no long-lived keys
              ADRs: ADR-007 | Tests: pipeline lint, dry run

[ ] TASK-045  Canary deploy and auto-rollback
              Lambda alias with weighted routing: 10% new, 90% previous
              CloudWatch alarm on error_rate_high triggers alias rollback
              Rollback completes within 5 minutes of alarm trigger
              ADRs: none | Tests: inject synthetic errors, verify rollback

**Phase 7 Gate**: MR to main triggers full pipeline end-to-end. Auto-rollback tested.

---

## Phase 8 — Async and Long-Running Agents

[ ] TASK-046  Write src/async-runner/handler.py
              NOT SQS-triggered — this is a background task within Runtime session
              Agent code uses app.add_async_task to start background work
              Agent code uses app.complete_async_task when done
              /ping returns HealthyBusy during background work
              Bridge Lambda submits invocation and polls for session completion
              Writes JOB record updates as status progresses
              ADRs: ADR-010 | Tests: HealthyBusy ping, task completion

[ ] TASK-047  Write src/webhook-delivery/handler.py
              EventBridge rule on DynamoDB Stream for JOB table status=complete
              POST to registered webhookUrl with HMAC-SHA256 signature
              Retry: 3 attempts, exponential backoff (2s, 4s, 8s)
              On exhaustion: update JOB record, alert ops
              ADRs: ADR-010 | Tests: signature verification, retry behaviour

[ ] TASK-048  Job polling and webhook registration APIs
              GET  /v1/jobs/{jobId}: status, presigned result URL when complete
              POST /v1/webhooks: register callback URL
              DELETE /v1/webhooks/{id}: deregister
              ADRs: ADR-010 | Tests: status transitions, presigned URL expiry

**Phase 8 Gate**: Async echo-agent variant completes simulated 30-second background
task. Result delivered via webhook and available via poll endpoint.

---

## Blocked / Future

[!] TASK-049  Account vending Terraform (Option B topology)
              BLOCKED: Trigger is ConcurrentSessions > 70% quota. Not yet reached.
              ADRs: ADR-009

[!] TASK-050  A2A cross-agent orchestration
              BLOCKED: Awaiting AWS GA of A2A protocol in eu-west-1

[!] TASK-051  AgentCore Policy CEDAR enforcement
              BLOCKED: Policy GA not available in eu-west-1 or eu-west-2
              Currently: Bedrock Guardrails in prod

[ ] TASK-052  Billing metering pipeline
              Daily Lambda aggregates token counts per tenant
              Applies pricing model from SSM per tier
              Writes BILLING_SUMMARY to DynamoDB
              Suspends tenant on budget exceeded
              Phase: after Phase 7

[ ] TASK-053  Tenant self-service portal
              Currently: operator-provisioned via Admin API
              Future: tenant admin can invite users, rotate API keys, view usage
              Phase: backlog
