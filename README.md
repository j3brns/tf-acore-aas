# LoopaaS — A forkable Agent as a Service franchise 
## A franchise wrapping Amazon Bedrock AgentCore's IaC bones

Amateur production multi-tenant **Agent as a Service** boot and eval framing on Amazon Bedrock AgentCore.

Tenants invoke AI agents via REST API in captured portal, with full tenant isolation, billing attribution,
and compliance controls baked in. Agent developer teams push new agents independently
via a local stack .. And a super fast self-service pipeline — no platform release management required.  But please don't test by fisjing

![Platform architecture showing eu-west-2 control plane, eu-west-1 compute, and eu-central-1 evaluation regions](docs/images/tf_acore_aas_architecture.drawio.png)

## Highlights

- **Multi-tenant REST API** — per-request data isolation enforced at four independent layers
- **Entra ID OIDC + SigV4** — human and machine auth; no Cognito anywhere
- **Three invocation modes** — sync (15 min), streaming SSE (15 min), async with webhooks (8 hr)
- **Self-service agent pipeline** — `make agent-push` supports a fast path when dependencies are unchanged
- **SPA frontend** — React app with OIDC login, streaming responses, session keepalive
- **EU-only data residency** — current approved topology keeps data in eu-west-2 London and runtime in eu-west-1 Dublin (~12ms RTT)
- **LocalStack DevX** — full local inner loop without AWS credentials

## Portal Experience

The SPA is the operator and tenant-facing control surface for the platform. It covers:

- **Tenant dashboard** — daily usage, budget posture, tier/status, and quick actions for keys, members, webhooks, and audit export
- **Platform admin** — cross-region health, quota headroom, tenant portfolio status, and operator actions
- **Members and invites** — tenant-scoped user access and invitation workflow
- **Webhooks** — async job callback registration and lifecycle management
- **Invoke flow** — prompt submission, streaming or async status tracking, and session continuity

Portal previews in the docs:

- [Tenant dashboard preview](docs/images/tf_acore_aas_portal_tenant_dashboard.svg)
- [Admin overview preview](docs/images/tf_acore_aas_portal_admin_overview.svg)
- [Members and invites preview](docs/images/tf_acore_aas_portal_members.svg)
- [Webhooks preview](docs/images/tf_acore_aas_portal_webhooks.svg)
- [Invoke flow preview](docs/images/tf_acore_aas_portal_invoke.svg)

These are fixture-based documentation renders derived from the current SPA page structure, not live production screenshots.

## Quick Start

**Prerequisites**: [uv](https://docs.astral.sh/uv/) (>=0.4), Docker (>=24), AWS CLI v2, Node 20 LTS, npm, GitLab access, Entra group membership.

```bash
git clone <repo> && cd tf-acore-aas
cp .env.example .env.local    # Fill in ENTRA_CLIENT_ID, ENTRA_TENANT_ID, API_BASE_URL
make bootstrap                # Checks prereqs, installs Python + Node deps
make dev                      # Starts LocalStack + mock Runtime + mock JWKS
make dev-invoke               # Confirms echo-agent works end-to-end locally
```

| Next step | Guide |
|-----------|-------|
| Full local environment | [Local Development Setup](docs/development/LOCAL-SETUP.md) |
| First AWS deployment | [Bootstrap Guide](docs/bootstrap-guide.md) |
| Entra app registration | [Entra Setup](docs/entra-setup.md) |

## Architecture

> Full details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | All diagrams: [Diagram Catalog](docs/README.md#diagram-catalog)

### Region Topology

| Region | Role | Key Services |
|--------|------|-------------|
| **eu-west-2** London | HOME — control + data plane | REST API Gateway, WAF, CloudFront, DynamoDB, S3, Secrets Manager, SSM, all Lambdas, KMS |
| **eu-west-1** Dublin | COMPUTE — current primary runtime region by platform policy | AgentCore Runtime (arm64 Firecracker), Observability, Browser, Code Interpreter |
| **eu-central-1** Frankfurt | EVALUATION + failover | AgentCore Evaluations, runtime failover target |

AWS documentation now shows AgentCore Runtime and related core services available in
multiple EU regions, including London, Dublin, and Frankfurt, but this platform still
runs the London-to-Dublin zigzag topology adopted in ADR-009. That deployment policy
remains in place pending an explicit architecture review and controlled migration decision.

### Request Lifecycle

![Synchronous request lifecycle: client through CloudFront, API Gateway, Authoriser, Bridge, Runtime, Gateway interceptors, and back](docs/images/tf_acore_aas_request_lifecycle_engineer.drawio.png)

Client → CloudFront → API Gateway (WAF + usage plan) → **Authoriser** (JWT validation, tenant context)
→ **Bridge** (tenant role assumption, runtime dispatch) → **AgentCore Runtime** (Firecracker microVM)
→ **Gateway interceptors** (act-on-behalf tokens, tier filtering) → Tool Lambdas → response stream back.

### Tenant Isolation (Defence in Depth)

| Layer | Component | Enforcement |
|-------|-----------|-------------|
| 1 | REST API Authoriser | Validates JWT, rejects invalid/suspended tenants |
| 2 | Bridge Lambda | Assumes tenant-specific IAM execution role |
| 3 | Gateway Interceptors | Issues scoped act-on-behalf token, tier-filtered tools |
| 4 | data-access-lib | `TenantScopedDynamoDB` raises `TenantAccessViolation` on cross-tenant access |

A single-layer breach does not compromise tenant data.

### Entity Lifecycle

![State transitions for tenants, agents, invocations, jobs, and sessions](docs/images/tf_acore_aas_entities_state_diagram.drawio.png)

### CDK Stack Dependencies

![CDK stack deployment order and cross-stack resource wiring](docs/images/tf_acore_aas_cdk_stack_dependencies.drawio.png)

NetworkStack → IdentityStack → PlatformStack → TenantStack *(per-tenant, event-driven)* → ObservabilityStack → AgentCoreStack.

## Project Structure

```
tf-acore-aas/
├── CLAUDE.md                  AI coding assistant rules
├── Makefile                   All dev, test, ops, and deploy commands
├── .env.example               Required environment variables
├── .githooks/                 Repo-local Git hooks (pre-push validation)
│
├── docs/                      Documentation suite
│   ├── README.md              Index, diagram catalog, role-based reading guide
│   ├── ARCHITECTURE.md        System design, data model, failure modes
│   ├── PLAN.md                Phased delivery plan with gates
│   ├── ROADMAP.md             Vision, milestones M1–M7, V1.x backlog
│   ├── TASKS.md               Task snapshot (GitHub Issues are canonical)
│   ├── bootstrap-guide.md     Day-zero deployment
│   ├── entra-setup.md         Entra app registration
│   ├── decisions/             ADR-001..014
│   ├── operations/            RUNBOOK-000..009
│   ├── security/              Threat model, compliance checklist
│   ├── development/           Local setup, agent developer guide
│   └── images/                Diagrams (.drawio + PNG/SVG exports)
│
├── agents/                    Agent implementations
│   └── echo-agent/            Reference agent — copy to create new agents
├── gateway/                   AgentCore Gateway interceptor Lambdas
├── src/                       Platform Lambda functions
│   ├── authoriser/            JWT token authoriser
│   ├── bridge/                Agent invocation bridge
│   ├── bff/                   Token refresh + session keepalive
│   ├── tenant_api/            Tenant CRUD API
│   ├── billing/               Billing and metering handlers
│   ├── webhook_delivery/      Async result delivery
│   └── data-access-lib/       Tenant-scoped DynamoDB/S3 library package
├── spa/                       React SPA frontend
├── infra/
│   ├── cdk/                   CDK stacks (TypeScript strict)
│   └── terraform/             Account vending only
├── scripts/                   Ops, bootstrap, agent packaging
└── tests/                     Integration and cross-cutting tests
```

## Development Workflow

### Getting started

```bash
make bootstrap                # one-time: checks prereqs, installs all deps
make install-git-hooks        # one-time: installs pre-push hook (fast validation)
make dev                      # start LocalStack + mock services
make test-unit                # run all unit tests
make validate-local           # ruff + pyright + tsc + cdk synth + detect-secrets
```

### Working on issues (canonical flow)

All work is tracked via [GitHub Issues](https://github.com/j3brns/tf-acore-aas/issues)
using `Seq:` for ordering and `Depends on:` for dependency gating.

```bash
make issue-queue              # dependency-aware queue ordered by Seq
make worktree-next-issue      # create worktree for next runnable issue
make worktree                 # interactive worktree menu
make preflight-session        # branch/issue policy checks
make pre-validate-session     # fast pre-push validation (no cdk synth)
make worktree-push-issue      # push with preflight + pre-validate enforced
```
### Agent developer inner loop

```bash
make agent-push AGENT=my-agent ENV=dev    # push agent, fast path when deps are unchanged
make agent-invoke AGENT=my-agent PROMPT="hello"
make agent-test AGENT=my-agent
```

### Frontend developer inner loop

```bash
make spa-dev                              # start local SPA dev server against mock API
make spa-push ENV=dev                     # build and push SPA to S3 + invalidate CloudFront
```

### Operations
See [Agent Developer Guide](docs/development/AGENT-DEVELOPER-GUIDE.md) for full details.

### Operations

```bash
make ops-top-tenants ENV=prod             # top tenants by invocation volume
make ops-quota-report ENV=prod            # AgentCore quota utilisation
make ops-backfill-tenant-role-arn APPLY=1  # backfill tenant execution roles
make failover-lock-acquire && \
  make infra-set-runtime-region REGION=eu-central-1 ENV=prod
```

See [Operator Runbooks](docs/operations/) for incident procedures.

## Contributing

1. **Pick an issue**: `make issue-queue` shows the next runnable issue
2. **Create a worktree**: `make worktree-create-issue ISSUE=<N>`
3. **Implement and test**: write code, run `make test-unit`, iterate
4. **Validate**: `make preflight-session && make pre-validate-session`
5. **Push**: `make worktree-push-issue` (enforces preflight + validation)
6. **Open PR**: link the issue; CI runs full validation

Platform Lambda source directories use `snake_case`. The shared
`src/data-access-lib/` workspace is the existing tenant-scoped data access package.
See [CLAUDE.md](CLAUDE.md) for full conventions and branch naming patterns.

## Technology Stack

| Concern | Technology |
|---------|-----------|
| Agent runtime | Amazon Bedrock AgentCore Runtime (arm64 Firecracker; current primary runtime region: eu-west-1) |
| Human auth | Microsoft Entra ID OIDC |
| Machine auth | AWS SigV4 |
| IaC (platform) | AWS CDK, TypeScript strict mode |
| IaC (accounts) | Terraform HCL |
| Python tooling | uv + pyproject.toml |
| Logging | aws-lambda-powertools Logger (structured JSON) |
| CDK testing | Jest + cdk-assertions |
| Python testing | pytest + LocalStack |
| Secrets | AWS Secrets Manager |
| Configuration | AWS SSM Parameter Store |
| Async agents | AgentCore `add_async_task` / `complete_async_task` SDK |
| Observability | AgentCore Observability + Amazon CloudWatch |

## Key Documents

| Document | Audience | Description |
|----------|----------|-------------|
| [Documentation Suite](docs/README.md) | All | Entry point, diagram catalog, role-based reading guide |
| [Portal Previews](docs/README.md#portal-page-previews) | Engineers / QA / Ops | Fixture-based previews of the tenant and admin SPA views |
| [Architecture](docs/ARCHITECTURE.md) | Engineers | System topology, data model, scaling, failure modes |
| [Roadmap](docs/ROADMAP.md) | All | Vision, milestones M1–M7, V1.x backlog |
| [Delivery Plan](docs/PLAN.md) | Engineers | Phased plan with gates and success criteria |
| [Bootstrap Guide](docs/bootstrap-guide.md) | Ops | Day-zero environment deployment |
| [Entra Setup](docs/entra-setup.md) | Ops | Entra app registration |
| [Agent Developer Guide](docs/development/AGENT-DEVELOPER-GUIDE.md) | Agent devs | Build, test, and push agents |
| [Local Setup](docs/development/LOCAL-SETUP.md) | Engineers | Full local development environment |
| [Threat Model](docs/security/THREAT-MODEL.md) | Security | Threat analysis and mitigations |
| [Compliance Checklist](docs/security/COMPLIANCE-CHECKLIST.md) | Security | Controls and evidence tracking |
| [Operator Runbooks](docs/operations/) | Ops | RUNBOOK-000..009 incident procedures |
| [Architecture Decisions](docs/decisions/) | Engineers | ADR-001..014 |
| [GitHub Issues](https://github.com/j3brns/tf-acore-aas/issues) | All | Canonical task queue |

## Contacts

| Role | Contact |
|------|---------|
| Platform team | team-platform |
| Security | team-security |
| On-call ops | PagerDuty |
