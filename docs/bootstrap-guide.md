# Bootstrap Guide

## Overview

This guide covers first-time deployment of the platform to a new AWS environment.
Follow RUNBOOK-000 for the actual execution steps. This guide explains the
prerequisites and manual steps that cannot be automated.

## AWS Account Setup

The platform requires two AWS accounts at minimum:
- **platform-control**: eu-west-2 (London) — data plane, all application services
- **platform-runtime**: eu-west-1 (Dublin) — AgentCore Runtime compute

Both accounts should be under an AWS Organization for SCP enforcement.
For eu-central-1 (Frankfurt) — Evaluations/Policy — the platform-runtime account
is used unless you have a third dedicated account.

Record the account IDs — needed for CDK bootstrap and cross-account IAM trust.

## Entra App Registration (manual — see entra-setup.md)

This step cannot be automated. An Entra admin must create the app registration
before bootstrap can run. See docs/entra-setup.md for full instructions.

Once complete, record:
- Application (client) ID
- Directory (tenant) ID
- Client secret value (set in Secrets Manager during bootstrap step 2)

## GitLab OIDC (partially manual)

`make bootstrap-gitlab-oidc` creates the OIDC provider and pipeline roles automatically.
However, the role ARNs must be added to GitLab CI/CD variables manually (UI-only operation).

After running the command, the ARNs are printed to the console. Add them to:
GitLab → Project → Settings → CI/CD → Variables:
- PLATFORM_PIPELINE_VALIDATE_ROLE_ARN
- PLATFORM_PIPELINE_DEPLOY_DEV_ROLE_ARN
- PLATFORM_PIPELINE_DEPLOY_STAGING_ROLE_ARN
- PLATFORM_PIPELINE_DEPLOY_PROD_ROLE_ARN

## What bootstrap.py Does

Ordered steps with validation at each:

1. **Verify prerequisites** — checks all required tools and account IDs
2. **CDK bootstrap** — creates CDKToolkit stacks in eu-west-2, eu-west-1, eu-central-1
3. **Seed secrets** — writes Entra credentials and platform private key to Secrets Manager
4. **OIDC wiring** — creates GitLab OIDC provider and pipeline roles, prints ARNs
5. **First CDK deploy** — deploys all 6 stacks from local machine (not pipeline)
6. **Post-deploy seeding** — creates first admin, seeds SSM, registers echo-agent
7. **Smoke test** — invokes echo-agent, checks alarms, confirms quota headroom
8. **Delete bootstrap user** — removes temporary IAM user (MANDATORY)

Each step writes to bootstrap-report.json (S3 bucket: platform-bootstrap-reports-{env}).

## Time Estimates

| Step                  | Duration     |
|-----------------------|--------------|
| Entra app registration| 15–30 min (manual) |
| CDK bootstrap         | 5 min        |
| Secrets seeding       | 2 min        |
| GitLab OIDC + manual  | 5 min + 5 min (manual) |
| First CDK deploy      | 15–20 min    |
| Post-deploy seeding   | 2 min        |
| Smoke test            | 5 min        |
| **Total**             | **~55 min**  |

## Re-Running Bootstrap

Each step in bootstrap.py is idempotent — safe to re-run if a step fails.
The script checks what already exists before creating resources.

To re-run a specific step using the corresponding make target:
```bash
make bootstrap-secrets ENV=dev          # re-run step: seed-secrets
make bootstrap-gitlab-oidc ENV=dev      # re-run step: gitlab-oidc
make bootstrap-post-deploy ENV=dev      # re-run step: post-deploy
make bootstrap-verify ENV=dev           # re-run step: verify
```

Or call bootstrap.py directly with the step name:
```bash
uv run python scripts/bootstrap.py --step seed-secrets --env dev
```

## Destroying an Environment

```bash
make infra-destroy ENV=dev
# Destroys all CDK stacks
# WARNING: destroys all data including DynamoDB tables and S3 buckets
# Run only on dev — never on prod
```

Re-bootstrapping after destroy takes ~35 minutes (CDK re-uses existing ECR/S3 ARNs).
