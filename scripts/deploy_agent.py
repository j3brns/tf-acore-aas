"""
deploy_agent.py — Deploy agent code to AgentCore Runtime.

Usage:
    uv run python scripts/deploy_agent.py <agent_name> --env <env>
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

try:
    from agent_manifest import ManifestValidationError, load_agent_manifest
except ImportError:
    from scripts.agent_manifest import ManifestValidationError, load_agent_manifest

logger = logging.getLogger("deploy_agent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = REPO_ROOT / ".build"


def require_aws_region() -> str:
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION must be set")
    return region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy agent code")
    parser.add_argument("agent_name", help="Name of the agent")
    parser.add_argument("--env", required=True, choices=["dev", "staging", "prod"])
    return parser.parse_args()


def get_ssm_param(ssm, name: str) -> str | None:
    try:
        response = ssm.get_parameter(Name=name)
        return response["Parameter"]["Value"]
    except ClientError as e:
        error = e.response.get("Error", {})
        if error.get("Code") == "ParameterNotFound":
            return None
        raise


def update_agentcore_runtime(
    acore, agent_name: str, bucket: str, deps_key: str, script_key: str
) -> str:
    response = acore.update_agent_code(
        agentName=agent_name,
        code={
            "s3Bucket": bucket,
            "depsKey": deps_key,
            "scriptKey": script_key,
        },
    )
    runtime_arn = str(response.get("agentArn", "")).strip()
    if not runtime_arn:
        raise RuntimeError("AgentCore update_agent_code did not return agentArn")
    return runtime_arn


def deploy_agent(agent_name: str, env: str) -> bool:
    try:
        manifest = load_agent_manifest(agent_name, REPO_ROOT)
    except ManifestValidationError as exc:
        for error in exc.errors:
            logger.error(error)
        return False

    aws_region = require_aws_region()

    # Resolve bucket (same as build_layer)
    from build_layer import resolve_layer_bucket

    bucket = resolve_layer_bucket(env, aws_region)

    ssm = boto3.client("ssm", region_name=aws_region)
    deps_key = get_ssm_param(ssm, f"/platform/layers/{env}/{agent_name}/s3-key")
    if not deps_key:
        logger.error(
            f"Deps not found in SSM for agent '{agent_name}' in env '{env}'. Run build_layer first."
        )
        return False

    code_zip = BUILD_DIR / f"{agent_name}-code.zip"
    if not code_zip.exists():
        logger.error(f"Agent code zip not found: {code_zip}. Run package_agent first.")
        return False

    script_key = f"scripts/{agent_name}/{manifest.version}.zip"
    s3 = boto3.client("s3", region_name=aws_region)
    logger.info(f"Uploading agent code to s3://{bucket}/{script_key}")
    s3.upload_file(str(code_zip), bucket, script_key)

    # Update script-s3-key in SSM (essential for register_agent)
    ssm.put_parameter(
        Name=f"/platform/agents/{env}/{agent_name}/script-s3-key",
        Value=script_key,
        Type="String",
        Overwrite=True,
    )

    try:
        acore = boto3.client("bedrock-agentcore", region_name=aws_region)
        logger.info(f"Updating AgentCore Runtime for '{agent_name}'")
        runtime_arn = update_agentcore_runtime(acore, agent_name, bucket, deps_key, script_key)
    except Exception as e:
        logger.error("Failed to update AgentCore Runtime for '%s': %s", agent_name, e)
        return False

    ssm.put_parameter(
        Name=f"/platform/agents/{env}/{agent_name}/runtime-arn",
        Value=runtime_arn,
        Type="String",
        Overwrite=True,
    )

    logger.info(f"Agent '{agent_name}' deployed successfully to {env}")
    return True


if __name__ == "__main__":
    args = parse_args()
    if not deploy_agent(args.agent_name, args.env):
        import sys

        sys.exit(1)
