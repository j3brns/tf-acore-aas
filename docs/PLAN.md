# Delivery Plan

> This is the phased delivery baseline for the platform. Implementation in the
> repository has moved beyond the original documentation-only starting point in
> several areas, but milestone gates remain open until their acceptance criteria
> pass cleanly.

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
- First successful `make infra-deploy ENV=dev` execution

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
- Native AgentCore async lifecycle via `app.add_async_task` / `app.complete_async_task`
- Webhook delivery Lambda with HMAC-SHA256 signing
- Job polling API (GET /v1/jobs/{jobId})
- Webhook registration API

**Gate**: 8-hour async agent completes. Result delivered via webhook and poll endpoint.

---

## Backlog (Not in MVP scope)

- Account vending Terraform (Option B/C topology) — triggers at 70% quota utilisation
- A2A cross-agent orchestration — delivered in TASK-050 (2026-03-10)
- AgentCore Policy CEDAR enforcement — implemented on 2026-03-10 (Issue #58)
- Agent marketplace and catalogue portal
- Tenant self-service portal (currently operator-provisioned)
- Revisit eu-west-2 Runtime placement — AWS now supports London; current zigzag remains in place pending explicit architecture review and migration plan

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

---

## Appendix — SPA Target State

Current maturity baseline as of 2026-03-10:

- Engineering MVP: approximately 65%
- Production B2B portal: approximately 40%
- Ideal-state product/site: approximately 25-30%

The gap is not that the SPA is absent. The gap is that the current SPA is still
an engineering surface rather than a finished tenant and operator product.

### Design Principles

1. Long-running work must feel safe.
   Streaming, async jobs, token refresh, and session continuity must behave as
   one coherent product flow rather than isolated components.
2. Tenant and operator experiences must diverge cleanly.
   Tenant users need clarity, self-service, and trust. Operators need density,
   evidence, and controlled action surfaces.
3. The shell must be role-aware and mobile-complete.
   Navigation cannot disappear on smaller viewports or expose irrelevant routes.
4. Trust cues must be explicit.
   The product should show environment, tenant, region, session state, and data
   handling expectations where the user needs them.
5. Product content is part of the system.
   Empty states, permissions messaging, failure copy, onboarding, and help text
   are part of operability.
6. Accessibility is a release gate.
   Keyboard navigation, focus order, live regions for streaming, and readable
   contrast must be designed in, not added later.

### Target Navigation

#### Shared

| Area | Purpose |
|------|---------|
| Sign In | Human entry point using Entra |
| Onboarding | First-run orientation and permission checks |
| Notifications | Cross-product status, success, warning, and error messaging |
| Profile / Session | Current identity, tenant, role, and sign-out |
| Status / Maintenance | Platform-wide incident and maintenance communication |

#### Tenant

| Area | Purpose |
|------|---------|
| Dashboard | Usage, active jobs, session health, quota, and current issues |
| Agents | Discover available agents and their capabilities |
| Invoke | Primary execution workspace for sync, streaming, and async use |
| Jobs | Historic and active async execution tracking |
| Sessions | Runtime session visibility and keepalive state |
| Usage & Billing | Consumption, budget, tier, and invoice-facing summaries |
| API Keys | Rotation and lifecycle management |
| Members & Invites | Tenant user access control |
| Webhooks | Async result destinations and delivery history |
| Audit Exports | Export requests, status, and evidence retrieval |
| Settings | Tenant profile and platform-facing configuration |
| Help / Support | Documentation, contact path, and troubleshooting guidance |

#### Operator

| Area | Purpose |
|------|---------|
| Platform Overview | High-level health, active incidents, and rollout state |
| Tenants | Search, filter, inspect, and act on tenant records |
| Runtime Regions | Active region, failover posture, and regional health |
| Quota & Capacity | Utilisation, trend, and account-topology trigger visibility |
| Incidents | Open events, timelines, and runbook entry points |
| Security Events | Auth failures, policy denials, tenant violations, audit flags |
| DLQs / Failed Deliveries | Operational backlog and replay surfaces |
| Jobs / Webhooks | Cross-tenant async execution and delivery visibility |
| Configuration / Rollouts | Environment version, rollout state, and change evidence |
| Audit / Evidence | Exportable records for compliance and incident review |

### Route Model

| Route | Audience | Notes |
|-------|----------|-------|
| `/` | Shared | Role-aware landing route; redirects to dashboard when signed in |
| `/onboarding` | Shared | First-run checks and setup guidance |
| `/agents` | Tenant | Catalogue and discovery |
| `/agents/:agentName` | Tenant | Agent detail, examples, constraints, supported modes |
| `/invoke/:agentName` | Tenant | Dedicated execution workspace |
| `/jobs` | Tenant | Job history and active work |
| `/jobs/:jobId` | Tenant | Job detail and result retrieval |
| `/sessions` | Tenant | Session list, health, expiry, and keepalive state |
| `/tenant` | Tenant | Tenant dashboard |
| `/tenant/usage` | Tenant | Usage and billing detail |
| `/tenant/api-keys` | Tenant | API key lifecycle |
| `/tenant/members` | Tenant | Members, invites, roles |
| `/tenant/webhooks` | Tenant | Registered endpoints and delivery state |
| `/tenant/audit` | Tenant | Audit export requests and download history |
| `/tenant/settings` | Tenant | Tenant settings |
| `/help` | Shared | Docs, support, troubleshooting |
| `/admin` | Operator | Platform overview |
| `/admin/tenants` | Operator | Tenant search, list, drill-down |
| `/admin/tenants/:tenantId` | Operator | Tenant detail and operator actions |
| `/admin/runtime` | Operator | Runtime regions, failover, quotas |
| `/admin/incidents` | Operator | Incident board and runbook links |
| `/admin/security` | Operator | Security events and policy signals |
| `/admin/deliveries` | Operator | DLQs and webhook failures |
| `/admin/changes` | Operator | Rollouts, versions, evidence |
| `/admin/audit` | Operator | Audit and evidence exports |

### Page Responsibilities

#### Tenant

| Page | Must Show | Primary Actions |
|------|-----------|-----------------|
| Dashboard | today's usage, budget posture, active jobs, session status, recent failures | resume work, open jobs, open support |
| Agents | agent cards, tier requirement, invocation mode, summary | inspect, invoke |
| Agent Detail | description, supported modes, expected duration, examples, limits, tool access notes | invoke, copy example |
| Invoke Workspace | prompt composer, agent metadata, stream state, async state, retries, errors, history sidebar | submit, cancel, retry, open job |
| Jobs | filterable list, status, durations, result readiness, webhook state | open job, retry delivery when permitted |
| Job Detail | timeline, result links, delivery attempts, error detail | download result, copy request context |
| Sessions | active sessions, expiry, runtime region, keepalive status | refresh state, inspect session |
| Usage & Billing | usage trends, budget, tier, expected billing summary | export usage, request upgrade |
| API Keys | current key metadata, last rotation, policy notes | rotate, revoke, copy integration guidance |
| Members & Invites | users, roles, pending invites, expiry | invite, revoke, resend |
| Webhooks | endpoints, secret posture, delivery health | add endpoint, rotate secret, disable |
| Audit Exports | export history, status, retention, destination | request export, download |
| Settings | tenant metadata, support contacts, region posture | update allowed fields |
| Help / Support | docs links, troubleshooting, contact path | open doc, contact support |

#### Operator

| Page | Must Show | Primary Actions |
|------|-----------|-----------------|
| Platform Overview | global health, active incidents, quota hot spots, runtime region, deployment version | drill into issue, open runbook |
| Tenants | filterable tenant list, tier, status, region, budget posture | inspect tenant, suspend or resume where policy allows |
| Tenant Detail | tenant metadata, recent activity, sessions, jobs, security signals, audit path | rotate key, resend invite, export evidence |
| Runtime Regions | active runtime region, failover state, quota, alarms, recent failovers | trigger guarded failover, inspect evidence |
| Incidents | open events, severity, owner, timeline | acknowledge, open runbook |
| Security Events | auth failures, policy denials, access violations | inspect, export, pivot to tenant |
| DLQs / Failed Deliveries | pending failures, age, retry state | replay, inspect payload metadata |
| Jobs / Webhooks | async backlog, webhook health, failure hotspots | inspect, replay when allowed |
| Configuration / Rollouts | deployed version, rollout state, validation evidence | inspect change, rollback entry point |
| Audit / Evidence | export jobs, compliance evidence, incident pack status | request export, download evidence |

### Key Wireframe Structures

1. Application shell
   Top bar: environment banner, current tenant, current role, notification tray, profile menu.
   Navigation: role-aware, responsive, always available on mobile via drawer or bottom sheet.
   Main area: page header, status chips, primary action, content area, optional context rail.
2. Invoke workspace
   Left rail: agent summary, examples, invocation mode, recent prompts.
   Main composer: prompt editor and submit controls.
   Stream pane: live response, connection state, elapsed time, retry or reconnect cues.
   Async panel: job identifier, progress state, poll cadence, webhook or download outcome.
   Evidence footer: invocation metadata, runtime region, correlation IDs, copy support payload.
3. Tenant dashboard
   Summary cards, work queue, operational alerts, and quick actions.
4. Operator overview
   Global status strip, hotspot grid, action queue, and tenant spotlight.

### Delivery Slices

1. Session continuity and invoke resilience.
   Align browser flows with ADR-011 and make long-running streaming and async work safe to operate.
   Related issues: `#166`, `#167`, `#168`.
2. App shell and route model.
   Introduce responsive navigation, role-aware routing, notifications, and a production landing model.
   Related issues: `#163`, `#169`.
3. Tenant self-service.
   Complete members, API keys, usage, webhooks, audit, and settings surfaces.
   Related issues: `#170`.
4. Operator operations console.
   Expand the admin view into a real operations product.
   Related issues: `#171`.
5. Design system and content.
   Replace generic internal styling and placeholder copy with a coherent product system and accessibility baseline.
   Related issues: `#172`.

### Acceptance Criteria

The SPA is not considered production-finished until all items below are true:

1. The BFF-backed token refresh and keepalive path is used for the intended long-running browser flows.
2. Deep links and hard refreshes work correctly behind CloudFront.
3. Mobile navigation exists and supports the full route model.
4. Tenant admins can complete common self-service tasks without operator help.
5. Operators can triage common runtime, tenant, and delivery problems from the UI.
6. The product exposes clear trust, status, and support cues.
7. Accessibility, contract, and component tests cover the critical UI paths.
