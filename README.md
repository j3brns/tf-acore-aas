<p align="center">
  <img src="docs/images/a5c-cell-readme-intro.png" alt="a5c-cell - Agentic Infrastructure Framework" width="100%" max-width="900px">
# Agentic Infrastructure Framework: **a5c-cell**
</p>


## Atomic AWS service cells for agent workloads

**a5c-cell** is a personal exploratory project: Production-informed, its a free formed framework for packaging agent workloads into repeatable, operable service cells on AWS-managed infrastructure.

> _**a5c??** Yes, its not a commit fragment "a5c" is just a barely necessary typographic abbreviation of *agentic*. It makes more sense with k8s, but I did need a name.._

At its core, this project asks a practical question: can a bootable, paved, end-to-end stack for operations, tooling, agent development inner loops, and tenancy act as a reusable cellular platform layer, and is that layer worth the operational overhead when compared with runbooks, SOPs, and business-as-usual DevOps procedure?

> The squeeze is real: overhead, maintenance, roadmap pressure, resourcing, operational demarcation, developer experience, inner loop speed, lifecycle management of hosted agents, and the continuing care and feeding of the framework itself.

Each **a5c-cell** adds a microservice control layer over Amazon Bedrock and AgentCore to provide each cell with partitionable tenancy, operational tooling, logging, control points, a fast development inner loop, and for human users - a scaffolded single-page application exposure layer, on Cloudfront.

# Who builds it, who runs it? 
Normally same as; Who decides its Prod ready? No one, in this case, so I built as if to happily transfer between my alter-ego's for 100 days. 

> I'm actually a firm believer in the mentality **"You build it , YOU run it"** exacts. New tech needs to be unerpinned by sharp end skill, and Devs are usually first to hit the 'unsupported yet' wall!

_The aim was not merely to prototype, but to test whether an operable task and automation layer for agent workloads could survive realistic operating conditions.

The result is a practical Ops CLI and runbook model that supports federated ownership, clearer standardisation, and better alignment with emerging AI operations practice._

> **Scaling demarcation (concept)**  
> Every **a5c-cell** maps 1:1 to:
> - an AWS account
> - a service boundary
> - an operations and accounting unit
> - a resource namespace
> - a resource boundary
> - a fixed service allow list

## Operational model

Tenants invoke AI agents through a controlled REST interface exposed through the portal, with tenant isolation, billing attribution, and compliance controls designed in from the start.

Agent teams can push and iterate on agents independently through a responsive inner-loop harness, including local stack support for development and test.

> In practice, this creates a fast self-service path that separates agent code from heavier platform dependencies. Sub-production releases and aliased challengers can move without waiting for a full outer-loop platform release.

_Useful?, certainly. Also the sort of thing that encourages dangerous optimism. Please do not test in production. Not yet._

![Platform architecture showing eu-west-2 control plane, eu-west-1 compute, and eu-central-1 evaluation regions](docs/images/tf_acore_aas_architecture.drawio.png)

## Highlights

- **Multi-tenant REST API** — per-request tenant isolation enforced across four independent control layers
- **Entra ID OIDC and SigV4** — human and machine authentication, with no Cognito dependency
- **Three invocation modes** — synchronous up to 15 minutes, streaming SSE up to 15 minutes, and asynchronous execution with webhooks up to 8 hours
- **Self-service agent pipeline** — `make agent-push` supports a fast path when dependencies are unchanged
- **SPA frontend** — React application with OIDC login, streaming responses, and session keepalive
- **EU-only data residency** — approved topology keeps data in London and runtime in Dublin, with evaluation capability in Frankfurt
- **LocalStack developer loop** — full local inner loop without AWS credentials

## Portal experience

The SPA is the operator and tenant-facing control surface for the platform. It provides:

- **Tenant dashboard** — daily usage, budget posture, tier and status, and quick actions for keys, members, webhooks, and audit export
- **Platform admin** — cross-region health, quota headroom, tenant portfolio state, and operator actions
- **Members and invites** — tenant-scoped user access and invitation workflow
- **Webhooks** — asynchronous callback registration and lifecycle management
- **Invoke flow** — prompt submission, streaming responses or async tracking, and session continuity

Portal previews in the documentation:

- [Tenant dashboard preview](docs/images/tf_acore_aas_portal_tenant_dashboard.svg)
- [Admin overview preview](docs/images/tf_acore_aas_portal_admin_overview.svg)
- [Members and invites preview](docs/images/tf_acore_aas_portal_members.svg)
- [Webhooks preview](docs/images/tf_acore_aas_portal_webhooks.svg)
- [Invoke flow preview](docs/images/tf_acore_aas_portal_invoke.svg)

These are fixture-based documentation renders derived from the current SPA page structure, not live production screenshots.

## Quick start

**Prerequisites**: [uv](https://docs.astral.sh/uv/) 0.4 or later, Docker 24 or later, AWS CLI v2, Node 20 LTS, npm, GitLab access, and the required Entra group membership.

```bash
git clone <repo> && cd tf-acore-aas
cp .env.example .env.local    # Set ENTRA_CLIENT_ID, ENTRA_TENANT_ID, API_BASE_URL
make bootstrap                # Check prerequisites and install Python and Node dependencies
make dev                      # Start LocalStack, mock Runtime, and mock JWKS
make dev-invoke               # Confirm echo-agent works end-to-end locally
```

| Next step | Guide |
|-----------|-------|
| Full local environment | [Local Development Setup](docs/development/LOCAL-SETUP.md) |
| First AWS deployment | [Bootstrap Guide](docs/bootstrap-guide.md) |
| Entra app registration | [Entra Setup](docs/entra-setup.md) |

## Architecture

> Full details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)  
> Diagram catalogue: [docs/README.md#diagram-catalog](docs/README.md#diagram-catalog)

### Region topology

| Region | Role | Key services |
|--------|------|-------------|
| **eu-west-2** London | HOME — control and data plane | REST API Gateway, WAF, CloudFront, DynamoDB, S3, Secrets Manager, SSM, Lambda, KMS |
| **eu-west-1** Dublin | COMPUTE — current primary runtime region by platform policy | AgentCore Runtime arm64 Firecracker, Observability, Browser, Code Interpreter |
| **eu-central-1** Frankfurt | EVALUATION and failover | AgentCore Evaluations, runtime failover target |

AWS documentation now shows AgentCore Runtime and related core services available in multiple EU regions, including London, Dublin, and Frankfurt. This platform, however, still operates the London-to-Dublin topology defined in ADR-009. That deployment policy remains in place pending explicit architecture review and a controlled migration decision.

### Request lifecycle

![Synchronous request lifecycle: client through CloudFront, API Gateway, Authoriser, Bridge, Runtime, Gateway interceptors, and back](docs/images/tf_acore_aas_request_lifecycle_engineer.drawio.png)

Client → CloudFront → API Gateway with WAF and usage plan → **Authoriser** for JWT validation and tenant context → **Bridge** for tenant role assumption and runtime dispatch → **AgentCore Runtime** in Firecracker microVM → **Gateway interceptors** for act-on-behalf tokens and tier filtering → Tool Lambdas → response stream returned to client.

### Tenant isolation

Tenant isolation is enforced in depth across four layers:

| Layer | Component | Enforcement |
|-------|-----------|-------------|
| 1 | REST API Authoriser | Validates JWT and rejects invalid or suspended tenants |
| 2 | Bridge Lambda | Assumes tenant-specific IAM execution role |
| 3 | Gateway Interceptors | Issues scoped act-on-behalf token and tier-filtered tool access |
| 4 | data-access-lib | `TenantScopedDynamoDB` raises `TenantAccessViolation` on cross-tenant access |

A single-layer failure is not sufficient to compromise tenant data boundaries.

### Entity lifecycle

![State transitions for tenants, agents, invocations, jobs, and sessions](docs/images/tf_acore_aas_entities_state_diagram.drawio.png)

### CDK stack dependencies

![CDK stack deployment order and cross-stack resource wiring](docs/images/tf_acore_aas_cdk_stack_dependencies.drawio.png)

`NetworkStack` → `IdentityStack` → `PlatformStack` → `TenantStack` per tenant, event-driven → `ObservabilityStack` → `AgentCoreStack`

## Project structure

```text
tf-acore-aas/
├── CLAUDE.md                  AI coding assistant rules
├── Makefile                   Dev, test, ops, and deploy commands
├── .env.example               Required environment variables
├── .githooks/                 Repository-local Git hooks
│
├── docs/                      Documentation suite
│   ├── README.md              Index, diagram catalogue, role-based reading guide
│   ├── ARCHITECTURE.md        System design, data model, failure modes
│   ├── PLAN.md                Phased delivery plan with gates
│   ├── ROADMAP.md             Vision, milestones M1–M7, V1.x backlog
│   ├── TASKS.md               Task snapshot; GitHub Issues are canonical
│   ├── bootstrap-guide.md     Day-zero deployment
│   ├── entra-setup.md         Entra app registration
│   ├── decisions/             ADR-001..014
│   ├── operations/            RUNBOOK-000..009
│   ├── security/              Threat model, compliance checklist
│   ├── development/           Local setup, agent developer guide
│   └── images/                Diagrams and exported assets
│
├── agents/                    Agent implementations
│   └── echo-agent/            Reference agent template
├── gateway/                   AgentCore Gateway interceptor Lambdas
├── src/                       Platform Lambda functions
│   ├── authoriser/            JWT token authoriser
│   ├── bridge/                Agent invocation bridge
│   ├── bff/                   Token refresh and session keepalive
│   ├── tenant_api/            Tenant CRUD API
│   ├── billing/               Billing and metering handlers
│   ├── webhook_delivery/      Async result delivery
│   └── data-access-lib/       Tenant-scoped DynamoDB and S3 access library
├── spa/                       React SPA frontend
├── infra/
│   ├── cdk/                   CDK stacks in strict TypeScript
│   └── terraform/             Account vending only
├── scripts/                   Ops, bootstrap, and agent packaging
└── tests/                     Integration and cross-cutting tests
```

## Development workflow

### Getting started

```bash
make bootstrap                # One-time: check prerequisites and install dependencies
make install-git-hooks        # One-time: install pre-push hook
make dev                      # Start LocalStack and mock services
make test-unit                # Run all unit tests
make validate-local           # ruff + pyright + tsc + cdk synth + detect-secrets
```

### Working on issues

All work is tracked through [GitHub Issues](https://github.com/j3brns/tf-acore-aas/issues), using `Seq:` for ordering and `Depends on:` for dependency gating.

```bash
make issue-queue              # Dependency-aware queue ordered by Seq
make worktree-next-issue      # Create worktree for next runnable issue
make worktree                 # Interactive worktree menu
make preflight-session        # Branch and issue policy checks
make pre-validate-session     # Fast pre-push validation without cdk synth
make worktree-push-issue      # Push with preflight and validation enforced
```

### Agent developer inner loop

```bash
make agent-push AGENT=my-agent ENV=dev
make agent-invoke AGENT=my-agent TENANT=t-test-001 PROMPT="hello"
make agent-test AGENT=my-agent
```

`make agent-push` uses the fast path when dependencies are unchanged, which keeps the inner loop quick without bypassing the platform boundary entirely.

### Frontend developer inner loop

```bash
make spa-dev
make spa-push ENV=dev
```

`make spa-dev` starts the local SPA development server against the mock API.  
`make spa-push` builds and publishes the SPA to S3, then invalidates CloudFront.

### Operations

```bash
make ops-top-tenants ENV=prod
make ops-quota-report ENV=prod
make ops-backfill-tenant-role-arn APPLY=1
make failover-lock-acquire && \
  make infra-set-runtime-region REGION=eu-central-1 ENV=prod
```

See [Operator Runbooks](docs/operations/) for incident procedures and operational detail.

## Contributing

1. Pick an issue: `make issue-queue`
2. Create a worktree: `make worktree-create-issue ISSUE=<N>`
3. Implement and test: write code, run `make test-unit`, iterate
4. Validate: `make preflight-session && make pre-validate-session`
5. Push: `make worktree-push-issue`
6. Open a pull request and link the issue; CI runs full validation

Platform Lambda source directories use `snake_case`. The shared `src/data-access-lib/` workspace is the tenant-scoped data access package. See [CLAUDE.md](CLAUDE.md) for conventions and branch naming patterns.

## Technology stack

| Concern | Technology |
|---------|-----------|
| Agent runtime | Amazon Bedrock AgentCore Runtime arm64 Firecracker; current primary runtime region is eu-west-1 |
| Human authentication | Microsoft Entra ID OIDC |
| Machine authentication | AWS SigV4 |
| Platform IaC | AWS CDK with strict TypeScript |
| Account IaC | Terraform HCL |
| Python tooling | uv and `pyproject.toml` |
| Logging | aws-lambda-powertools Logger structured JSON |
| CDK testing | Jest and cdk-assertions |
| Python testing | pytest and LocalStack |
| Secrets | AWS Secrets Manager |
| Configuration | AWS SSM Parameter Store |
| Async agents | AgentCore `add_async_task` and `complete_async_task` SDK |
| Observability | AgentCore Observability and Amazon CloudWatch |

## Key documents

| Document | Audience | Description |
|----------|----------|-------------|
| [Documentation Suite](docs/README.md) | All | Entry point, diagram catalogue, role-based reading guide |
| [Portal previews](docs/README.md#portal-page-previews) | Engineers, QA, Ops | Fixture-based previews of tenant and admin SPA views |
| [Architecture](docs/ARCHITECTURE.md) | Engineers | System topology, data model, scaling, and failure modes |
| [Roadmap](docs/ROADMAP.md) | All | Vision, milestones M1–M7, and V1.x backlog |
| [Delivery Plan](docs/PLAN.md) | Engineers | Phased plan with gates and success criteria |
| [Bootstrap Guide](docs/bootstrap-guide.md) | Ops | Day-zero environment deployment |
| [Entra Setup](docs/entra-setup.md) | Ops | Entra application registration |
| [Agent Developer Guide](docs/development/AGENT-DEVELOPER-GUIDE.md) | Agent developers | Build, test, and push agents |
| [Local Setup](docs/development/LOCAL-SETUP.md) | Engineers | Full local development environment |
| [Threat Model](docs/security/THREAT-MODEL.md) | Security | Threat analysis and mitigations |
| [Compliance Checklist](docs/security/COMPLIANCE-CHECKLIST.md) | Security | Controls and evidence tracking |
| [Operator Runbooks](docs/operations/) | Ops | Incident procedures and operational runbooks |
| [Architecture Decisions](docs/decisions/) | Engineers | ADR-001..014 |
| [GitHub Issues](https://github.com/j3brns/tf-acore-aas/issues) | All | Canonical task queue |

## Contacts

| Role | Contact |
|------|---------|
| Faith | Hope |
