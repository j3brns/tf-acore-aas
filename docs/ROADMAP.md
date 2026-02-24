# Roadmap

## North Star
A fully managed, self-service Agent as a Service platform. B2B tenants invoke
specialised AI agents without managing infrastructure. Enterprise-grade isolation,
compliance, and billing. Operable by a small team without developers on-call.

## Current Status
Phase 0 — Foundation (documentation and decisions)

---

## MVP (Milestones 1–4)

### M1: Local Development Loop Working
Phase 2 complete. Acceptance: `make dev && make test-unit` both pass.
Echo agent invocable end-to-end locally.

### M2: Deployed to Dev AWS Account
Phase 3 complete. Acceptance: `make infra-deploy ENV=dev` succeeds.
Operator can run RUNBOOK-001 in dev.

### M3: Operable by Ops Team
Phase 4 complete. Acceptance: all runbooks executable by operator using
make targets only. No AWS console access required for any runbook.

### M4: Agent Developer Self-Service
Phase 5 complete. Acceptance: agent push <30s warm. All three invocation
modes work end-to-end. Interceptors enforce tier-based tool access.

---

## V1.0 (Milestones 5–7)

### M5: Tenant-Facing SPA
Phase 6 complete. Acceptance: tenant logs in via Entra, invokes agent,
sees streaming response in browser.

### M6: Automated Pipeline
Phase 7 complete. Acceptance: MR to main triggers full pipeline.
Auto-rollback tested in staging.

### M7: Long-Running Async Agents
Phase 8 complete. Acceptance: 8-hour async agent completes,
result delivered via webhook and poll endpoint.

---

## V1.x Backlog

| Item                            | Trigger / Dependency                           |
|---------------------------------|------------------------------------------------|
| Option B account topology       | ConcurrentSessions > 70% of account quota      |
| Billing metering pipeline       | After V1.0 — token cost attribution per tenant |
| Tenant self-service portal      | After V1.0 — tenant manages own API keys/users |
| A2A cross-agent orchestration   | AWS GA of A2A in eu-west-1                     |
| AgentCore Policy CEDAR          | AWS GA of Policy in eu-west-1                  |
| Agent marketplace               | After V1.0 — discovery and composition portal  |
| eu-west-2 Runtime               | AWS extends AgentCore to London                |

---

## Not In Scope (deliberate exclusions)

- Multi-cloud deployment (AWS only)
- Self-hosted AgentCore (managed service is the point)
- Per-tenant custom LLM model selection (platform chooses model per tier)
- Real-time analytics dashboard for tenants (batch reports only in V1)
