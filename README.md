# AgentCore Multi-Tenant AaaS Platform

## What This Is

An enterprise-grade Agent as a Service platform running on Amazon Bedrock AgentCore.
B2B tenants invoke AI agents via REST API with full isolation, billing attribution,
and compliance controls. Internal agent developer teams push new agents independently
via a self-service pipeline.

## Quick Start

Prerequisites: uv, Docker, AWS CLI v2, Node 20 LTS, GitLab access, Entra group membership.

```bash
git clone <repo>
cd platform
cp .env.example .env.local    # Fill in Entra client ID, tenant ID, API base URL
make bootstrap                # Checks prereqs, installs deps
make dev                      # Starts LocalStack + mock Runtime + mock JWKS
make dev-invoke               # Confirms echo-agent works end-to-end locally
```

See docs/development/LOCAL-SETUP.md for full setup instructions.
See docs/bootstrap-guide.md for first-time environment deployment.

## Project Structure

```
platform/
├── CLAUDE.md                  AI assistant rules — read first every session
├── README.md                  This file
├── Makefile                   All development and operations commands
├── .env.example               Required environment variable template
├── docs/
│   ├── PLAN.md                Phased delivery plan and milestones
│   ├── TASKS.md               Atomic task list for Claude Code sessions
│   ├── ARCHITECTURE.md        System design, data flows, constraints
│   ├── bootstrap-guide.md     Day-zero platform deployment
│   ├── entra-setup.md         Entra app registration instructions
│   ├── decisions/             Architecture Decision Records (ADR-001..013)
│   ├── operations/            Operator runbooks (RUNBOOK-001..009)
│   ├── security/              Threat model, compliance checklist
│   └── development/           Agent developer guide, local setup
├── agents/                    Agent implementations (one directory per agent)
│   └── echo-agent/            Reference agent — copy this to create new agents
├── gateway/                   AgentCore Gateway interceptor Lambdas
├── src/                       Platform Lambda functions
│   ├── authoriser/            REST API token authoriser
│   ├── bridge/                Agent invocation bridge
│   ├── bff/                   Token refresh and session keepalive
│   ├── tenant-api/            Tenant CRUD API
│   ├── async-runner/          Long-running agent job processor
│   ├── webhook-delivery/      Async job result delivery
│   └── data-access-lib/       Tenant-scoped DynamoDB/S3 library
├── spa/                       React SPA frontend
├── infra/
│   ├── cdk/                   CDK stacks (TypeScript strict)
│   └── terraform/             Account vending only
├── scripts/                   Ops, bootstrap, agent packaging
└── tests/                     Integration and cross-cutting tests
```

## Development Workflow

```bash
# Local inner loop
make dev                      # Start environment
make test-unit                # Run all unit tests
make validate-local           # fast local checks: ruff + pyright + tsc + cdk synth + detect-secrets (diff)
make validate-local-full      # same, but full-repo secret scan

# Agent developer inner loop
make agent-push AGENT=my-agent ENV=dev    # Push agent, fast path <30s if deps cached
make agent-invoke AGENT=my-agent PROMPT="hello"
make agent-test AGENT=my-agent

# Operations
make ops-top-tenants ENV=prod
make ops-quota-report ENV=prod
make failover-lock-acquire && make infra-set-runtime-region REGION=eu-central-1 ENV=prod
```

## Architecture Summary

- **Home region**: eu-west-2 London — data plane, control plane, all data
- **Compute region**: eu-west-1 Dublin — AgentCore Runtime (12ms RTT from London)
- **Evaluations/Policy**: eu-central-1 Frankfurt only
- **Auth**: Microsoft Entra ID OIDC for humans, SigV4 for machines
- **Isolation**: Tenant context enforced at authoriser, bridge, interceptor, and data layers
- **Async agents**: AgentCore SDK `app.add_async_task` pattern — session stays HealthyBusy

## Contacts

| Role              | Contact          |
|-------------------|------------------|
| Platform team     | team-platform    |
| Security          | team-security    |
| On-call ops       | PagerDuty        |

## Key Documents

- [Architecture](docs/ARCHITECTURE.md)
- [Delivery Plan](docs/PLAN.md)
- [Task List](docs/TASKS.md)
- [Bootstrap Guide](docs/bootstrap-guide.md)
- [Operator Runbooks](docs/operations/)
- [Agent Developer Guide](docs/development/AGENT-DEVELOPER-GUIDE.md)
- [Threat Model](docs/security/THREAT-MODEL.md)
