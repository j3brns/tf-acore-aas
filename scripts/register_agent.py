"""register_agent.py — Register or update agent in the platform registry.

Writes agent metadata to DynamoDB platform-agents table and SSM.
Reads [tool.agentcore] manifest from agent's pyproject.toml.

Registered attributes: agentName, version, ownerTeam, tierMinimum,
layerHash, layerS3Key, scriptS3Key, runtimeArn, deployedAt,
invocationMode, streamingEnabled, estimatedDurationSeconds.

Usage:
    uv run python scripts/register_agent.py <agent_name> --env <env>

Implemented in TASK-035.
ADRs: ADR-005, ADR-008
"""

import argparse
import os
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


def read_pyproject(agent_name: str, repo_root: Path) -> dict:
    toml_path = repo_root / "agents" / agent_name / "pyproject.toml"
    if not toml_path.exists():
        print(f"Error: pyproject.toml not found at {toml_path}")
        sys.exit(1)

    with toml_path.open("rb") as f:
        return tomllib.load(f)


def get_ssm_value(ssm, name: str) -> str | None:
    try:
        response = ssm.get_parameter(Name=name)
        return response["Parameter"]["Value"]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return None
        raise


def register_agent(agent_name: str, env: str, repo_root: Path | None = None) -> None:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[1]

    data = read_pyproject(agent_name, repo_root)
    config = data.get("tool", {}).get("agentcore", {})
    project = data.get("project", {})

    if not config:
        print(f"Error: [tool.agentcore] section missing in pyproject.toml for {agent_name}")
        sys.exit(1)

    version = project.get("version", "0.1.0")
    owner_team = config.get("owner_team", "unknown")
    tier_minimum = config.get("tier_minimum", "basic")
    invocation_mode = config.get("invocation_mode", "sync")
    estimated_duration = config.get("estimated_duration_seconds", 30)
    streaming_enabled = config.get("streaming_enabled", invocation_mode == "streaming")

    # AWS Region and clients
    region = os.environ.get("AWS_REGION", "eu-west-2")
    endpoint_url = os.environ.get("LOCALSTACK_ENDPOINT")

    client_kwargs: dict[str, Any] = {"region_name": region}
    if endpoint_url and endpoint_url != "mock":
        client_kwargs["endpoint_url"] = endpoint_url

    ssm = boto3.client("ssm", **client_kwargs)
    dynamodb = boto3.resource("dynamodb", **client_kwargs)

    # Retrieve layer hash and S3 keys from SSM (stored by build_layer.py and deploy_agent.py)
    layer_hash = get_ssm_value(ssm, f"/platform/layers/{agent_name}/hash") or "0" * 16
    layer_s3_key = get_ssm_value(ssm, f"/platform/layers/{agent_name}/s3-key") or ""
    script_s3_key = get_ssm_value(ssm, f"/platform/agents/{agent_name}/script-s3-key") or ""
    runtime_arn = get_ssm_value(ssm, f"/platform/agents/{agent_name}/runtime-arn") or ""

    deployed_at = datetime.now(UTC).isoformat()

    # 1. Update DynamoDB platform-agents table
    table = dynamodb.Table("platform-agents")
    item = {
        "PK": f"AGENT#{agent_name}",
        "SK": f"VERSION#{version}",
        "agent_name": agent_name,
        "version": version,
        "owner_team": owner_team,
        "tier_minimum": tier_minimum,
        "layer_hash": layer_hash,
        "layer_s3_key": layer_s3_key,
        "script_s3_key": script_s3_key,
        "runtime_arn": runtime_arn,
        "deployed_at": deployed_at,
        "invocation_mode": invocation_mode,
        "streaming_enabled": streaming_enabled,
        "estimated_duration_seconds": estimated_duration,
    }

    print(f"Registering agent {agent_name} v{version} in DynamoDB...")
    table.put_item(Item=item)

    # 2. Update SSM latest version pointer
    ssm.put_parameter(
        Name=f"/platform/agents/{agent_name}/latest-version",
        Value=version,
        Type="String",
        Overwrite=True,
    )

    print(f"Successfully registered {agent_name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register agent in platform registry")
    parser.add_argument("agent_name", help="Name of the agent")
    parser.add_argument("--env", required=True, help="Target environment")

    args = parser.parse_args()
    register_agent(args.agent_name, args.env)
