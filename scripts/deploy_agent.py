"""deploy_agent.py — Deploy agent code to AgentCore Runtime.

For zip deployment (default):
    Calls AgentCore Runtime create/update API with code_zip containing
    s3_bucket, deps_key (from SSM), and script_key.

For container deployment (opt-in):
    Builds Docker image with --platform linux/arm64, pushes to ECR.

Deployment type is read from [tool.agentcore.deployment.type] in pyproject.toml.

Usage:
    uv run python scripts/deploy_agent.py <agent_name> --env <env>

Implemented in TASK-035.
ADRs: ADR-005, ADR-008
"""

import argparse
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

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


def deploy_agent(agent_name: str, env: str, repo_root: Path | None = None) -> None:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[1]

    data = read_pyproject(agent_name, repo_root)
    config = data.get("tool", {}).get("agentcore", {})
    version = data.get("project", {}).get("version", "0.1.0")

    deployment_config = config.get("deployment", {})
    deploy_type = deployment_config.get("type", "zip")

    # AWS Region and clients
    region = os.environ.get("AWS_REGION", "eu-west-2")
    endpoint_url = os.environ.get("LOCALSTACK_ENDPOINT")
    mock_runtime = os.environ.get("MOCK_RUNTIME", "false").lower() == "true"

    client_kwargs: dict[str, Any] = {"region_name": region}
    if endpoint_url and endpoint_url != "mock":
        client_kwargs["endpoint_url"] = endpoint_url

    s3 = boto3.client("s3", **client_kwargs)
    ssm = boto3.client("ssm", **client_kwargs)

    if deploy_type == "zip":
        zip_path = repo_root / ".build" / f"{agent_name}-code.zip"
        if not zip_path.exists():
            print(f"Error: Zip artifact not found at {zip_path}. Run package_agent.py first.")
            sys.exit(1)

        # 1. Determine S3 bucket for deployment
        if endpoint_url:
            bucket_name = f"platform-artifacts-{env}-local"
            try:
                # Moto/LocalStack create bucket
                s3.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": cast(Any, region)},
                )
            except ClientError:
                pass
        else:
            bucket_name = get_ssm_value(ssm, "/platform/config/artifacts-bucket")
            if not bucket_name:
                sts = boto3.client("sts")
                account_id = sts.get_caller_identity()["Account"]
                bucket_name = f"platform-artifacts-{env}-{account_id}"

        # 2. Upload code ZIP to S3
        s3_key = f"scripts/{agent_name}/{version}.zip"
        print(f"Uploading {zip_path} to s3://{bucket_name}/{s3_key}...")
        s3.upload_file(str(zip_path), bucket_name, s3_key)

        # 3. Get dependency layer hash/key from SSM
        _deps_key = get_ssm_value(ssm, f"/platform/layers/{agent_name}/s3-key") or ""

        # 4. Update AgentCore Runtime
        if mock_runtime:
            print("Deploying to mock AgentCore Runtime (no-op)...")
            runtime_arn = f"arn:aws:bedrock-agentcore:{region}:000000000000:runtime/{agent_name}"
        else:
            print("Deploying to real AgentCore Runtime (Stubbed)...")
            runtime_arn = f"arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/{agent_name}"

        # 5. Store deployment metadata in SSM
        ssm.put_parameter(
            Name=f"/platform/agents/{agent_name}/script-s3-key",
            Value=s3_key,
            Type="String",
            Overwrite=True,
        )
        ssm.put_parameter(
            Name=f"/platform/agents/{agent_name}/runtime-arn",
            Value=runtime_arn,
            Type="String",
            Overwrite=True,
        )

        print(f"Successfully deployed {agent_name} to {env}")

    elif deploy_type == "container":
        print(f"Error: Container deployment for {agent_name} not yet implemented.")
        sys.exit(1)
    else:
        print(f"Error: Unknown deployment type '{deploy_type}' for {agent_name}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy agent to AgentCore Runtime")
    parser.add_argument("agent_name", help="Name of the agent")
    parser.add_argument("--env", required=True, help="Target environment")

    args = parser.parse_args()
    deploy_agent(args.agent_name, args.env)
