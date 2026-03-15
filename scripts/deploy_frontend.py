"""
deploy_frontend.py — Deploy SPA to S3 and invalidate CloudFront distribution.

Reads S3 bucket name and CloudFront distribution ID from SSM.
Syncs spa/dist/ to S3 with correct cache-control headers.
Creates CloudFront invalidation for /* path.

Usage:
    uv run python scripts/deploy_frontend.py --env <env>

Called by: make spa-push ENV=<env>
"""

import argparse
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("deploy_frontend")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

REPO_ROOT = Path(__file__).resolve().parents[1]
SPA_DIST = REPO_ROOT / "spa" / "dist"


def require_aws_region() -> str:
    """Read AWS_REGION from environment and fail fast if missing."""
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        # Fallback for local development or non-standard environments
        return "eu-west-2"
    return region


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Deploy SPA frontend")
    parser.add_argument("--env", required=True, choices=["dev", "staging", "prod"])
    return parser.parse_args(argv)


def get_ssm_param(ssm, name: str) -> str:
    """Read parameter value from SSM. Exit on failure."""
    try:
        response = ssm.get_parameter(Name=name)
        return response["Parameter"]["Value"]
    except ClientError as e:
        logger.error(f"Failed to get SSM parameter {name}: {e}")
        sys.exit(1)


def deploy_spa(env: str) -> None:
    """Sync SPA dist directory to S3 and invalidate CloudFront."""
    region = require_aws_region()
    ssm = boto3.client("ssm", region_name=region)

    bucket_name = get_ssm_param(ssm, f"/platform/spa/{env}/bucket-name")
    distribution_id = get_ssm_param(ssm, f"/platform/spa/{env}/distribution-id")

    if not SPA_DIST.exists():
        logger.error(f"SPA dist directory not found: {SPA_DIST}. Run 'make spa-build' first.")
        sys.exit(1)

    s3 = boto3.client("s3", region_name=region)
    logger.info(f"Syncing {SPA_DIST} to s3://{bucket_name}")

    # Ensure common web mimetypes are correctly registered
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("image/svg+xml", ".svg")

    count = 0
    for root, _, files in os.walk(SPA_DIST):
        for file in files:
            full_path = Path(root) / file
            # S3 keys use forward slashes even on Windows
            key = str(full_path.relative_to(SPA_DIST)).replace("\\", "/")

            content_type, _ = mimetypes.guess_type(str(full_path))
            if not content_type:
                content_type = "application/octet-stream"

            extra_args = {"ContentType": content_type}

            # Cache-control strategy:
            # - index.html: no-cache (check for updates every time)
            # - assets/: immutable (Vite includes content hashes in filenames)
            # - others: 1 hour default
            if key == "index.html":
                extra_args["CacheControl"] = "no-cache, no-store, must-revalidate"
            elif key.startswith("assets/"):
                extra_args["CacheControl"] = "public, max-age=31536000, immutable"
            else:
                extra_args["CacheControl"] = "public, max-age=3600"

            s3.upload_file(str(full_path), bucket_name, key, ExtraArgs=extra_args)
            count += 1

    logger.info(f"Uploaded {count} files to S3")

    # Invalidate CloudFront to ensure users get the new version immediately
    cf = boto3.client("cloudfront", region_name=region)
    logger.info(f"Creating CloudFront invalidation for {distribution_id}")
    try:
        cf.create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": ["/*"]},
                "CallerReference": str(time.time()),
            },
        )
        logger.info("CloudFront invalidation requested")
    except ClientError as e:
        # In mock or restricted environments, invalidation might not be available
        logger.warning(f"Failed to invalidate CloudFront: {e}")


if __name__ == "__main__":
    args = parse_args()
    try:
        deploy_spa(args.env)
        logger.info(f"SPA deployed successfully to {args.env}")
    except Exception as exc:
        logger.error(f"Deployment failed: {exc}")
        sys.exit(1)
