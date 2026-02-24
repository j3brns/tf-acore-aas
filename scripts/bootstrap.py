"""
bootstrap.py — First-time platform bootstrap script.

Ordered steps with validation at each. Idempotent — safe to re-run.

Usage:
    uv run python scripts/bootstrap.py --step <step> --env <env>

Steps (run in order):
    cdk-bootstrap       CDK bootstrap all three regions
    seed-secrets        Write initial secrets to Secrets Manager
    gitlab-oidc         Create GitLab OIDC provider and pipeline roles
    post-deploy         Seed first admin, SSM params, register echo-agent
    verify              Smoke test the bootstrapped environment
    delete-bootstrap-user  Delete the temporary bootstrap IAM user (MANDATORY)

See docs/bootstrap-guide.md and RUNBOOK-000 for full instructions.

Implemented in TASK-028.
ADRs: ADR-007
"""
