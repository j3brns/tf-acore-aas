"""
rollback_agent.py — Roll back an agent to its previous deployed version.

Queries the platform-agents DynamoDB table for the previous version,
re-deploys that version to AgentCore Runtime, and updates the registry.

Usage:
    uv run python scripts/rollback_agent.py <agent_name> --env <env>

Called by: make agent-rollback AGENT=<name> ENV=prod
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# Reuse bucket resolution from build_layer
from build_layer import resolve_layer_bucket

logger = logging.getLogger("rollback_agent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

REPO_ROOT = Path(__file__).resolve().parents[1]


def require_aws_region() -> str:
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION must be set")
    return region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roll back an agent")
    parser.add_argument("agent_name", help="Name of the agent")
    parser.add_argument("--env", required=True, choices=["dev", "staging", "prod"])
    return parser.parse_args()


def get_agent_versions(table, agent_name: str) -> list[dict]:
    """Query DynamoDB for all versions of the agent, latest first."""
    try:
        response = table.query(
            KeyConditionExpression=Key("PK").eq(f"AGENT#{agent_name}"),
            ScanIndexForward=False,  # Highest version SK first
        )
        items = response.get("Items", [])
        return sorted(
            items, key=lambda x: (x.get("deployed_at", ""), x.get("version", "")), reverse=True
        )
    except ClientError as e:
        logger.error(f"Failed to query DynamoDB: {e}")
        return []


def rollback_agent(agent_name: str, env: str) -> bool:
    aws_region = require_aws_region()
    dynamodb = boto3.resource("dynamodb", region_name=aws_region)
    table = dynamodb.Table("platform-agents")

    versions = get_agent_versions(table, agent_name)
    if len(versions) < 2:
        logger.error(f"Rollback failed: No previous version found for agent '{agent_name}'.")
        return False

    current_version = versions[0]
    previous_version = versions[1]

    logger.info(
        f"Rolling back agent '{agent_name}' from v{current_version['version']} "
        f"(deployed {current_version.get('deployed_at')}) to v{previous_version['version']} "
        f"(deployed {previous_version.get('deployed_at')})"
    )

    bucket = resolve_layer_bucket(env, aws_region)
    deps_key = previous_version["layer_s3_key"]
    script_key = previous_version["script_s3_key"]
    version = previous_version["version"]
    layer_hash = previous_version["layer_hash"]

    ssm = boto3.client("ssm", region_name=aws_region)

    # 1. Update AgentCore Runtime
    try:
        acore = boto3.client("bedrock-agentcore", region_name=aws_region)
        logger.info(f"Updating AgentCore Runtime for '{agent_name}' to previous artifacts")
        response = acore.update_agent_code(
            agentName=agent_name,
            code={
                "s3Bucket": bucket,
                "depsKey": deps_key,
                "scriptKey": script_key,
            },
        )
        runtime_arn = response.get("agentArn")
        if runtime_arn:
            ssm.put_parameter(
                Name=f"/platform/agents/{env}/{agent_name}/runtime-arn",
                Value=runtime_arn,
                Type="String",
                Overwrite=True,
            )
    except Exception as e:
        logger.warning(f"Failed to call AgentCore Runtime API: {e}")
        if os.environ.get("CI"):
            logger.info("Continuing anyway because CI is set")

    # 2. Update SSM Registry
    logger.info("Updating SSM registry to previous version artifacts")
    ssm_updates = {
        f"/platform/agents/{env}/{agent_name}/latest-version": version,
        f"/platform/agents/{env}/{agent_name}/script-s3-key": script_key,
        f"/platform/layers/{env}/{agent_name}/hash": layer_hash,
        f"/platform/layers/{env}/{agent_name}/s3-key": deps_key,
    }

    for name, val in ssm_updates.items():
        try:
            ssm.put_parameter(Name=name, Value=val, Type="String", Overwrite=True)
        except ClientError as e:
            logger.error(f"Failed to update SSM parameter {name}: {e}")
            return False

    # 3. Delete the rolled-back version from DynamoDB
    logger.info(f"Deleting rolled-back version v{current_version['version']} from DynamoDB")
    try:
        table.delete_item(Key={"PK": current_version["PK"], "SK": current_version["SK"]})
    except ClientError as e:
        logger.error(f"Failed to delete bad version from DynamoDB: {e}")
        return False

    logger.info(f"Agent '{agent_name}' successfully rolled back to v{version}")
    return True


if __name__ == "__main__":
    args = parse_args()
    if not rollback_agent(args.agent_name, args.env):
        sys.exit(1)
