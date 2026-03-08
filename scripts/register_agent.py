"""
register_agent.py — Register or update agent in the platform registry.

Writes agent metadata to DynamoDB platform-agents table and SSM.
Reads [tool.agentcore] manifest from agent's pyproject.toml.

Usage:
    uv run python scripts/register_agent.py <agent_name> --env <env>
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import tomllib
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("register_agent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

_REPO_ROOT = Path(__file__).resolve().parents[1]


def require_aws_region() -> str:
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION must be set")
    return region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register agent")
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


def register_agent(agent_name: str, env: str, repo_root: Path | None = None) -> bool:
    aws_region = require_aws_region()
    root = repo_root or _REPO_ROOT
    toml_path = root / "agents" / agent_name / "pyproject.toml"
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})
    version = project.get("version", "1.0.0")
    manifest = data.get("tool", {}).get("agentcore", {})

    ssm = boto3.client("ssm", region_name=aws_region)
    layer_hash = get_ssm_param(ssm, f"/platform/layers/{agent_name}/hash")
    layer_s3_key = get_ssm_param(ssm, f"/platform/layers/{agent_name}/s3-key")

    if not layer_hash or not layer_s3_key:
        logger.error(f"Layer metadata not found for agent '{agent_name}'. Run build_layer first.")
        return False

    script_s3_key = f"agents/{agent_name}/code.zip"
    deployed_at = datetime.datetime.now(datetime.UTC).isoformat()

    # Get Runtime ARN from SSM if it exists (set by infra or previous deployment)
    runtime_arn = get_ssm_param(ssm, f"/platform/agents/{agent_name}/runtime-arn")

    item = {
        "PK": f"AGENT#{agent_name}",
        "SK": f"VERSION#{version}",
        "agent_name": agent_name,
        "version": version,
        "owner_team": manifest.get("owner_team", "unknown"),
        "tier_minimum": manifest.get("tier_minimum", "basic"),
        "layer_hash": layer_hash,
        "layer_s3_key": layer_s3_key,
        "script_s3_key": script_s3_key,
        "deployed_at": deployed_at,
        "invocation_mode": manifest.get("invocation_mode", "sync"),
        "streaming_enabled": manifest.get("streaming_enabled", False),
        "runtime_arn": runtime_arn,
        "estimated_duration_seconds": manifest.get("estimated_duration_seconds", 5),
    }

    table_name = "platform-agents"
    dynamodb = boto3.resource("dynamodb", region_name=aws_region)
    table = dynamodb.Table(table_name)

    logger.info(f"Registering agent '{agent_name}' v{version} in DynamoDB table '{table_name}'")
    table.put_item(Item=item)

    # Update latest-version in SSM
    ssm.put_parameter(
        Name=f"/platform/agents/{agent_name}/latest-version",
        Value=version,
        Type="String",
        Overwrite=True,
    )

    logger.info(f"Agent '{agent_name}' registered successfully")
    return True


if __name__ == "__main__":
    args = parse_args()
    if not register_agent(args.agent_name, args.env):
        import sys

        sys.exit(1)
