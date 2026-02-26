# AgentCore Saas-ified Cell Templates: Agents-aaT packaged for two-pizza teams to experiment. 

## What This Is

A small cell enterprise-style Agent as a Template platform vended on Amazon Bedrock AgentCore.
B2E tenant squads experiment with AI agents via REST API with full isolation, billing attribution,
and compliance controls. SPA annd OIDC included, along with a few sample Strands and 'bare' agents.

Internal agent developer teams are the owners and may push new agents independently
via a self-service pipeline. Identity and 3LO baked in, Strands keeps things simple. Langgraph when its not.

DevX, I've heard of it.. Inner-loop for responsive boto3 backed localstack experimentations.

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

Task tracking source of truth (effective 2026-02-25 13:00 local): GitHub Issues
(`Seq:` + `Depends on:` in issue bodies). `docs/TASKS.md` is now a snapshot/report.
Issue queue: https://github.com/j3brns/tf-acore-aas/issues

## Project Structure

```
platform/
├── CLAUDE.md                  AI assistant rules — read first every session
├── README.md                  This file
├── Makefile                   All development and operations commands
├── .githooks/                 Repo-local Git hooks (fast pre-push validation)
├── .env.example               Required environment variable template
├── docs/
│   ├── PLAN.md                Phased delivery plan and milestones
│   ├── TASKS.md               Historical task snapshot/report (Issues are canonical)
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
make install-git-hooks        # installs repo pre-push hook (fast validation, no cdk synth)

# Issue-driven worktree flow (canonical)
make issue-queue              # show queue ordered by Seq (dependency-aware)
make worktree-next-issue      # create worktree for next runnable issue
make worktree                 # interactive issue worktree menu
make preflight-session        # worktree branch/issue policy checks
make pre-validate-session     # fast pre-push validation (no cdk synth)
make worktree-push-issue      # push branch with preflight + pre-validate enforced

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
- [Task Snapshot](docs/TASKS.md)
- [GitHub Issues (canonical task queue)](https://github.com/j3brns/tf-acore-aas/issues)
- [Bootstrap Guide](docs/bootstrap-guide.md)
- [Operator Runbooks](docs/operations/)
- [Agent Developer Guide](docs/development/AGENT-DEVELOPER-GUIDE.md)
- [Threat Model](docs/security/THREAT-MODEL.md)
