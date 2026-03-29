# Roadmap

## North Star
A fully managed, self-service Agent as a Service platform. B2E users and E2B integrations invoke
specialised AI agents without managing infrastructure. Enterprise-grade isolation,
compliance, and billing. Operable by a small team without developers on-call.

## Current Status
Implementation has moved beyond the documentation-only foundation. The repository
now contains core Lambda handlers, CDK stacks, guard rules, gateway interceptors,
async bridge flows, and SPA code. Milestones below remain gated by their stated
acceptance criteria rather than by file presence alone.

---

## MVP (Milestones 1–4)

### M1: Local Development Loop Working
Substantial implementation present. Acceptance remains: `make dev && make test-unit`
both pass clean, with the echo agent invocable end-to-end locally.

### M2: Deployed to Dev AWS Account
CDK stacks, tests, and guard rules are present in-repo. Acceptance remains:
`make infra-deploy ENV=dev` succeeds and the operator can run RUNBOOK-001 in dev.

### M3: Operable by Ops Team
Runbooks and operator commands are present in-repo. Acceptance remains: all
runbooks are executable by the operator using make targets only, with no AWS
console access required for routine operations.

### M4: Agent Developer Self-Service
Packaging scripts, registration flow, and gateway interceptors are present.
Acceptance remains: agent push meets the warm-path target, all three invocation
modes work end-to-end, and interceptors enforce tier-based tool access.

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
| A2A cross-agent orchestration   | Delivered (TASK-050, 2026-03-10)               |
| AgentCore Policy CEDAR tuning   | Baseline shipped; next is fine-grained rules   |
| Agent marketplace               | After V1.0 — discovery and composition portal  |
| eu-west-2 Runtime migration     | Explicit architecture review and controlled migration off the current zigzag topology |

---

## Not In Scope (deliberate exclusions)

- Multi-cloud deployment (AWS only)
- Self-hosted AgentCore (managed service is the point)
- Per-tenant custom LLM model selection (platform chooses model per tier)
- Real-time analytics dashboard for tenants (batch reports only in V1)
