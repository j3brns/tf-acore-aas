# Delivery Plan

## Vision

A fully managed, self-service Agent as a Service platform enabling B2B tenants to invoke
specialised AI agents without managing infrastructure. Enterprise-grade isolation,
compliance, and billing. Operable by a small team without platform engineers on-call 24/7.

## Guiding Principle

Operability before features. A platform that ops can run reliably is worth more than
a platform with more features that falls over at 3am. Every phase must leave the system
more operable than it was before.

---

## Phase 0 — Foundation (Weeks 1–2)

**Goal**: All architectural decisions documented. No code exists. Everything that gets
built in Phase 1 onwards has a written rationale.

**Deliverables**:
- CLAUDE.md, README.md, ARCHITECTURE.md
- All 13 ADRs written and reviewed
- All 9 runbooks written (stubs are fine — they get refined as code is built)
- PLAN.md, TASKS.md, ROADMAP.md
- Makefile skeleton (all targets stubbed — none implemented)
- Empty directory tree with header comments

**Gate**: Platform engineer and operator both review and sign off all Phase 0 docs.
No Phase 1 work begins until this gate passes.

**Success criteria**:
- A new engineer can read Phase 0 docs and explain the system architecture
- An operator can read the runbooks and know what to do in each failure scenario
- All 13 ADRs explain why alternatives were rejected

---

## Phase 1 — Data Contracts (Weeks 2–3)

**Goal**: The data model and API contract are defined and reviewed before any Lambda
code is written. A wrong schema in Phase 1 is cheap. The same mistake discovered in
Phase 4 costs days.

**Deliverables**:
- DynamoDB table schemas as Python dataclasses (data_models.py)
- OpenAPI 3.1 spec for all northbound routes
- data-access-lib with TenantScopedDynamoDB and TenantScopedS3
- TenantAccessViolation exception with 100% test coverage

**Gate**: Data models and OpenAPI spec reviewed by platform engineer and operator.
Written confirmation required before Phase 2 begins.

**Success criteria**:
- `spectral lint` passes on OpenAPI spec
- TenantAccessViolation raised on every cross-tenant DynamoDB access attempt
- data-access-lib has 100% test coverage

---

## Phase 2 — Local Development Loop (Weeks 3–4)

**Goal**: A developer can clone the repo, run `make dev`, and invoke the echo agent
end-to-end in a local environment within 30 minutes of first checkout.

**Deliverables**:
- docker-compose.yml (LocalStack, mock AgentCore Runtime, mock JWKS)
- dev-bootstrap.py (idempotent seed script)
- All three core Lambdas: authoriser, bridge, tenant-api
- Full Makefile implementation
- echo-agent reference pattern with sync, streaming, and async modes demonstrated

**Gate**: `make dev && make test-unit` both pass clean.
Echo agent invocable end-to-end in local environment.

**Success criteria**:
- New developer can complete LOCAL-SETUP.md in under 30 minutes
- All three invocation modes work locally against mock Runtime
- Authoriser correctly rejects: expired JWT, wrong audience, cross-tenant header injection

---

## Phase 3 — CDK Infrastructure (Weeks 4–6)

**Goal**: The platform deploys to a real AWS dev account via CDK. An operator can
run `make infra-deploy ENV=dev` and get a working platform.

**Deliverables**:
- All 6 CDK stacks (Network, Identity, Platform, Tenant, Observability, AgentCore)
- cfn-guard rules for security policy enforcement
- CDK construct unit tests for every stack
- First successful `make bootstrap-dev` execution

**Gate**: `cdk synth` passes. `cfn-guard` passes. infra-diff reviewed by operator.
Operator runs RUNBOOK-001 (failover) in dev and it completes successfully.

**Success criteria**:
- All stacks deploy cleanly to dev account
- No wildcard IAM policies (cfn-guard enforces this)
- TenantStack deploys correctly for two test tenants
- Operator can suspend a tenant and reinstate it using make targets only

---

## Phase 4 — Bootstrap and Operations Tooling (Weeks 6–7)

**Goal**: An operator can bootstrap a new environment from scratch and respond to
every failure mode in the runbooks using make targets and the ops CLI alone.
No AWS console access required for routine operations.

**Deliverables**:
- bootstrap.py — ordered bootstrap sequence with validation at each step
- ops.py — full operations CLI wrapping the Admin REST API
- failover_lock.py — DynamoDB distributed lock for region failover
- All runbooks tested against dev environment
- RUNBOOK-000 (bootstrap) executable end-to-end

**Gate**: Operator completes full bootstrap of a new dev environment from scratch.
Operator completes every runbook scenario in dev. All pass.

**Success criteria**:
- `make bootstrap-verify ENV=dev` passes
- Every runbook has been executed at least once in dev
- Bootstrap IAM user deleted automatically at end of bootstrap
- Failover to Frankfurt and back completes without data loss

---

## Phase 5 — Agent Developer Experience (Weeks 7–9)

**Goal**: An agent developer can push an agent in under 30 seconds on the warm path
and under 2 minutes on the cold path. The pipeline promotes agents through dev →
staging → prod with evaluation gates.

**Deliverables**:
- Agent packaging scripts (hash, build, package, deploy, register)
- REQUEST and RESPONSE Gateway interceptors
- BFF Lambda (token refresh, session keepalive)
- .gitlab-ci-agent.yml agent pipeline
- Agent developer guide fully executable

**Gate**: `make agent-push AGENT=echo-agent ENV=dev` completes in <30s warm path.
All three invocation modes work end-to-end through real AWS infrastructure in dev.
Interceptors correctly enforce tier-based tool filtering.

**Success criteria**:
- Warm push (deps unchanged): <30 seconds
- Cold push (deps changed): <2 minutes
- Tier-insufficient tool access returns 403 before tool Lambda is invoked
- PII redaction working in RESPONSE interceptor

---

## Phase 6 — SPA Frontend (Weeks 9–11)

**Goal**: A tenant can log in via Entra, select an agent, invoke it, and see the
streaming response in a browser. An operator can see the platform health dashboard.

**Deliverables**:
- React SPA (Vite + TypeScript + Tailwind + shadcn)
- MSAL.js auth layer with token refresh
- Streaming response rendering (Fetch + ReadableStream)
- Async job polling component
- Admin view for operators (Platform.Operator role required)
- Deployed to S3 + CloudFront with CSP headers

**Gate**: Operator logs in via Entra, invokes echo-agent in all three modes, sees
results. Admin view shows platform health metrics.

---

## Phase 7 — CI/CD Pipeline (Weeks 11–13)

**Goal**: Merge to main triggers a full pipeline. Canary deploys to staging with
auto-rollback on error rate alarm. Two-reviewer approval for production.

**Deliverables**:
- Platform pipeline .gitlab-ci.yml (all stages)
- Agent pipeline .gitlab-ci-agent.yml
- Canary deploy config with 10% traffic split
- Auto-rollback wired to error_rate_high alarm
- cfn-guard rules integrated into validate stage

**Gate**: MR to main triggers full pipeline end-to-end without manual intervention.
Auto-rollback tested and verified in staging.

---

## Phase 8 — Async and Long-Running Agents (Weeks 13–16)

**Goal**: Agents using app.add_async_task can run for up to 8 hours.
Results delivered via webhook or poll endpoint.

**Deliverables**:
- async-runner Lambda with HealthyBusy ping handling
- Webhook delivery Lambda with HMAC-SHA256 signing
- Job polling API (GET /v1/jobs/{jobId})
- Webhook registration API

**Gate**: 8-hour async agent completes. Result delivered via webhook and poll endpoint.

---

## Backlog (Not in MVP scope)

- Account vending Terraform (Option B/C topology) — triggers at 70% quota utilisation
- A2A cross-agent orchestration — blocked on AWS GA in eu-west-1
- AgentCore Policy CEDAR enforcement — blocked on GA in eu-west-1
- Agent marketplace and catalogue portal
- Tenant self-service portal (currently operator-provisioned)

---

## Milestone Summary

| Milestone | End of Phase | Success Criteria                              |
|-----------|-------------|-----------------------------------------------|
| M1        | Phase 2     | make dev + make test-unit both pass           |
| M2        | Phase 3     | make infra-deploy ENV=dev succeeds            |
| M3        | Phase 4     | All runbooks executable by operator           |
| M4        | Phase 5     | Agent push <30s warm, pipeline end-to-end     |
| M5        | Phase 6     | Tenant invokes agent via browser              |
| M6        | Phase 7     | Pipeline auto-rollback verified in staging    |
| M7        | Phase 8     | 8-hour async agent delivers via webhook       |
