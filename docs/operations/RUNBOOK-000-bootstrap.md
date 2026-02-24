# RUNBOOK-000: Platform Bootstrap (Day Zero)

## Purpose
Bootstrap the platform from scratch into a new AWS environment. Run once per environment.

## Prerequisites
- AWS account IDs for: platform-control (eu-west-2), platform-runtime (eu-west-1)
- AWS CLI configured with bootstrap IAM user credentials (AdministratorAccess, temp)
- Node 20 LTS, uv, Docker, GitLab project access
- Entra app registration completed (see docs/entra-setup.md)
- Record: Entra client ID, Entra tenant ID, GitLab project ID

## Steps (run in order — each must succeed before proceeding)

### Step 1: CDK Bootstrap (all regions)
```bash
make bootstrap-cdk ENV=dev
# Runs: cdk bootstrap in eu-west-2, eu-west-1, eu-central-1
# Creates: CDKToolkit stacks in each region
# Expected output: Successfully bootstrapped (3 regions)
```

### Step 2: Seed Initial Secrets
```bash
make bootstrap-secrets ENV=dev
# Prompts for: Entra client ID, Entra client secret, platform private key passphrase
# Writes to Secrets Manager (never to git, never to env vars)
# Expected output: 4 secrets created in Secrets Manager
```

### Step 3: GitLab OIDC Wiring
```bash
make bootstrap-gitlab-oidc ENV=dev
# Creates OIDC provider in AWS IAM for gitlab.com
# Creates pipeline roles: platform-pipeline-validate-dev, -deploy-dev, etc.
# Outputs role ARNs to console
# MANUAL STEP: add role ARNs to GitLab CI/CD variables in GitLab UI
# Expected output: 5 IAM roles created, ARNs printed
```

### Step 4: First CDK Deploy (local, not pipeline)
```bash
make infra-deploy ENV=dev
# Runs cdk deploy --all from local machine using bootstrap IAM user
# Takes 15–20 minutes on first deploy
# Expected output: 6 stacks deployed successfully
```

### Step 5: Post-Deploy Seeding
```bash
make bootstrap-post-deploy ENV=dev
# Seeds first platform-admin user in DynamoDB
# Seeds SSM parameters
# Registers echo-agent in platform-agents DynamoDB table
# Expected output: Admin user created, echo-agent registered
```

### Step 6: Smoke Test
```bash
make bootstrap-verify ENV=dev
# Invokes echo-agent as admin user
# Checks all 10 FM alarms exist and are in OK state
# Checks quota headroom (should be near 0%)
# Prints: "Bootstrap complete. Delete bootstrap IAM user."
# Expected output: All checks pass
```

### Step 7: Delete Bootstrap IAM User (MANDATORY)
```bash
make bootstrap-delete-iam-user ENV=dev
# Deletes the bootstrap IAM user credentials
# From this point: all operations via OIDC or Entra
# CONFIRM: you can still access the platform via Entra login
```

## Post-Bootstrap
- Verify GitLab pipeline can deploy (push a trivial change to main)
- Confirm operator can log in via Entra with Platform.Operator role
- Record bootstrap-report.json S3 location for audit

## If Any Step Fails
- Each step is idempotent — safe to re-run after fixing the issue
- Check CloudFormation Events in the AWS console for CDK deploy failures
- Check scripts/bootstrap.py output for step-specific error messages

## Time Estimate
First run: approximately 45 minutes end-to-end.
Re-run after teardown: approximately 30 minutes (CDK re-uses existing ECR/S3).
