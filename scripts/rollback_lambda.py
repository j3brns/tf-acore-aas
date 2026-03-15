"""
rollback_lambda.py — Roll back a Lambda function to its previous alias version.

Updates the Lambda alias to point to the previous published version.
Used for emergency rollbacks without a full CDK deploy.

Usage:
    uv run python scripts/rollback_lambda.py <function_name> <env>

Example:
    uv run python scripts/rollback_lambda.py bridge prod

Called by: make infra-rollback-lambda FUNCTION=bridge ENV=prod
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("rollback_lambda")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def require_aws_region() -> str:
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION must be set")
    return region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roll back a Lambda function alias")
    parser.add_argument(
        "function_name", help="Name of the Lambda function (e.g. bridge, authoriser)"
    )
    parser.add_argument("env", choices=["dev", "staging", "prod"], help="Target environment")
    parser.add_argument("--alias", default="live", help="Alias name (default: live)")
    return parser.parse_args()


def get_full_function_name(function_base_name: str, env: str) -> str:
    """Resolve full Lambda function name based on platform naming convention."""
    # Pattern: platform-{resource}-{environment}
    return f"platform-{function_base_name}-{env}"


def rollback_lambda(function_name: str, env: str, alias_name: str) -> bool:
    aws_region = require_aws_region()
    client = boto3.client("lambda", region_name=aws_region)
    full_name = get_full_function_name(function_name, env)

    try:
        # 1. Get current alias configuration
        logger.info(f"Fetching current alias '{alias_name}' for function '{full_name}'")
        alias_resp = client.get_alias(FunctionName=full_name, Name=alias_name)
        current_version = alias_resp["FunctionVersion"]
        logger.info(f"Alias '{alias_name}' currently points to version {current_version}")

        # 2. List versions to find the previous one
        logger.info(f"Listing versions for function '{full_name}'")
        versions_resp = client.list_versions_by_function(FunctionName=full_name)
        versions = versions_resp.get("Versions", [])

        # Filter out $LATEST and sort by version number (descending)
        # Versions are strings, so we convert to int for sorting
        published_versions = []
        for v in versions:
            ver_str = v.get("Version")
            if ver_str and ver_str != "$LATEST":
                published_versions.append(int(ver_str))

        published_versions.sort(reverse=True)

        if not published_versions:
            logger.error("No published versions found for rollback.")
            return False

        # 3. Find target version
        current_ver_int = int(current_version) if current_version != "$LATEST" else None
        target_version = None

        if current_ver_int is None:
            # If alias points to $LATEST, target is the latest published version
            target_version = str(published_versions[0])
        else:
            # Find the highest version lower than current_ver_int
            for v in published_versions:
                if v < current_ver_int:
                    target_version = str(v)
                    break

        if not target_version:
            logger.error(
                f"Rollback failed: No previous version found lower than {current_version}."
            )
            return False

        # 4. Update alias
        logger.info(f"Rolling back alias '{alias_name}' to version {target_version}")
        client.update_alias(
            FunctionName=full_name,
            Name=alias_name,
            FunctionVersion=target_version,
            Description=f"Manual rollback from version {current_version} to {target_version}",
        )

        logger.info(
            f"Lambda '{full_name}' alias '{alias_name}' "
            f"successfully rolled back to v{target_version}"
        )
        return True

    except ClientError as e:
        logger.error(f"Failed to rollback Lambda: {e}")
        return False


if __name__ == "__main__":
    args = parse_args()
    if not rollback_lambda(args.function_name, args.env, args.alias):
        sys.exit(1)
