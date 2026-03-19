# Account Vending — Terraform

This directory contains the Terraform HCL for AWS Organizations account vending.
It is the only Terraform surface in this repository; all other infrastructure is
managed by CDK TypeScript (see `infra/cdk/`).

See [ADR-007](../../docs/decisions/ADR-007-cdk-terraform.md) for the split rationale.

## Purpose

When the platform reaches quota thresholds on the home account (Option A), operators
escalate to Option B (tier-split accounts) or Option C (per-tenant accounts) by
vending new AWS accounts via Organizations and wiring them into the platform.

See [RUNBOOK-002](../../docs/operations/RUNBOOK-002-quota-monitoring.md) and
[RUNBOOK-004](../../docs/operations/RUNBOOK-004-quota-increase.md) for the
operational triggers.

## Structure

```
infra/terraform/
├── main.tf                  Root module — provider, backend, Organizations data
├── variables.tf             Input variables
├── outputs.tf               Outputs (account IDs, role ARNs)
├── versions.tf              Required providers and Terraform version
├── modules/
│   └── vended-account/      Reusable module: one vended account
│       ├── main.tf
│       ├── variables.tf
│       └── outputs.tf
└── envs/
    ├── prod/
    │   └── terraform.tfvars
    └── staging/
        └── terraform.tfvars
```

## Usage

```bash
# Validate configuration (no AWS credentials required)
make tf-validate

# Plan changes (requires Organizations admin credentials)
make tf-plan ENV=prod

# Apply changes (requires Organizations admin credentials + operator approval)
make tf-apply ENV=prod
```

## State

Terraform state is stored in an S3 backend with DynamoDB locking, both in the
home region (eu-west-2). The state bucket and lock table are provisioned by
the platform CDK bootstrap step.

## Security

- No hardcoded account IDs, ARNs, or credentials in `.tf` files.
- Cross-account trust policies scope to the platform home account only.
- Vended accounts receive a minimal execution role for AgentCore Runtime invocation.
- All resources tagged with `platform:managed-by = terraform-account-vending`.
