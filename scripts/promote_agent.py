"""
promote_agent.py — Promote an agent version to RELEASED status.

Usage:
    uv run python scripts/promote_agent.py <agent_name> <version> --env <env>
    [--notes "Release notes"]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger("promote_agent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def _semver_sort_key(version: str) -> tuple[int, ...]:
    parts = version.split(".")
    key: list[int] = []
    for part in parts:
        digits = "".join(ch for ch in part if ch.isdigit())
        key.append(int(digits) if digits else 0)
    return tuple(key)


def require_aws_region() -> str:
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION must be set")
    return region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote agent version")
    parser.add_argument("agent_name", help="Name of the agent")
    parser.add_argument("version", help="Version to promote (semver)")
    parser.add_argument("--env", required=True, choices=["dev", "staging", "prod"])
    parser.add_argument("--notes", help="Release notes")
    return parser.parse_args()


def promote_agent(agent_name: str, version: str, env: str, notes: str | None) -> bool:
    aws_region = require_aws_region()
    dynamodb = boto3.resource("dynamodb", region_name=aws_region)
    table = dynamodb.Table("platform-agents")

    key = {"PK": f"AGENT#{agent_name}", "SK": f"VERSION#{version}"}

    logger.info(f"Promoting agent '{agent_name}' v{version} to RELEASED in {env}")

    try:
        # Check if version exists
        response = table.get_item(Key=key)
        if "Item" not in response:
            logger.error(f"Agent version {agent_name}:{version} not found in registry.")
            return False

        item = response["Item"]
        current_status = item.get("status", "released")
        if current_status == "released":
            logger.info(f"Agent version {agent_name}:{version} is already RELEASED.")
            return True

        # Update status
        attrs = {
            "status": "released",
            "updated_at": datetime.now(UTC).isoformat(),
            "approved_by": os.environ.get("USER", "cli-operator"),
            "approved_at": datetime.now(UTC).isoformat(),
        }
        if notes:
            attrs["release_notes"] = notes

        update_parts = []
        names = {}
        values = {}
        for i, (k, v) in enumerate(attrs.items()):
            n = f"#n{i}"
            val = f":v{i}"
            update_parts.append(f"{n} = {val}")
            names[n] = k
            values[val] = v

        table.update_item(
            Key=key,
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

        versions = table.query(KeyConditionExpression=Key("PK").eq(f"AGENT#{agent_name}"))
        released_versions = [
            str(item.get("version", ""))
            for item in versions.get("Items", [])
            if item.get("status", "released") == "released"
        ]
        latest_released_version = max(released_versions, key=_semver_sort_key, default=version)

        ssm = boto3.client("ssm", region_name=aws_region)
        ssm.put_parameter(
            Name=f"/platform/agents/{env}/{agent_name}/latest-version",
            Value=latest_released_version,
            Type="String",
            Overwrite=True,
        )

    except ClientError as e:
        logger.error(f"Failed to update DynamoDB or SSM: {e}")
        return False

    logger.info(f"Agent '{agent_name}' v{version} promoted successfully")
    return True


if __name__ == "__main__":
    args = parse_args()
    if not promote_agent(args.agent_name, args.version, args.env, args.notes):
        sys.exit(1)
