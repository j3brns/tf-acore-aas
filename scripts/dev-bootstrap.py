"""
dev-bootstrap.py — Local development environment seeding script.

Seeds LocalStack with:
  - Two test tenants: basic-tier (t-basic-001) and premium-tier (t-premium-001)
  - All SSM parameters pointing to LocalStack endpoints
  - DynamoDB tables with fixture data
  - Test JWTs written to .env.test

Idempotent — safe to run multiple times without creating duplicate records.

Usage:
    uv run python scripts/dev-bootstrap.py

Called automatically by: make dev

Implemented in TASK-015.
"""
