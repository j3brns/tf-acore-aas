"""
deploy_frontend.py — Deploy SPA to S3 and invalidate CloudFront distribution.

Reads S3 bucket name and CloudFront distribution ID from SSM.
Syncs spa/dist/ to S3 with correct cache-control headers.
Creates CloudFront invalidation for /* path.

Usage:
    uv run python scripts/deploy_frontend.py --env <env>

Called by: make spa-deploy ENV=<env>

Implemented in TASK-040.
"""
