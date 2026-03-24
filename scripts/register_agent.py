"""
register_agent.py — Register an immutable agent version in the platform registry.

Uses the Platform API (tenant-api) to register agent metadata.
Reads [tool.agentcore] manifest from agent's pyproject.toml.

Usage:
    uv run python scripts/register_agent.py <agent_name> --env <env>
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3
from botocore.exceptions import ClientError

try:
    from agent_manifest import ManifestValidationError, load_agent_manifest
except ImportError:
    from scripts.agent_manifest import ManifestValidationError, load_agent_manifest

logger = logging.getLogger("register_agent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

REPO_ROOT = Path(__file__).resolve().parents[1]


def require_aws_region() -> str:
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION must be set")
    return region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register agent")
    parser.add_argument("agent_name", help="Name of the agent")
    parser.add_argument("--env", required=True, choices=["dev", "staging", "prod"])
    parser.add_argument("--api-base-url", help="Override Platform API base URL")
    parser.add_argument("--token", help="Override Platform API access token")
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


def _request_api(
    url: str,
    method: str,
    token: str,
    body: dict | None = None,
) -> dict:
    data = json.dumps(body).encode("utf-8") if body else None
    request = Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_json = json.loads(error_body)
            message = error_json.get("message", error_body)
        except json.JSONDecodeError:
            message = error_body
        logger.error(f"API Error ({e.code}): {message}")
        raise RuntimeError(f"API Error {e.code}: {message}") from e
    except URLError as e:
        logger.error(f"Failed to reach API: {e.reason}")
        raise RuntimeError(f"Connection Error: {e.reason}") from e


def register_agent(agent_name: str, env: str, api_base_url: str | None, token: str | None) -> bool:
    try:
        manifest = load_agent_manifest(agent_name, REPO_ROOT)
    except ManifestValidationError as exc:
        for error in exc.errors:
            logger.error(error)
        return False

    aws_region = require_aws_region()
    ssm = boto3.client("ssm", region_name=aws_region)

    layer_hash = get_ssm_param(ssm, f"/platform/layers/{env}/{agent_name}/hash")
    layer_s3_key = get_ssm_param(ssm, f"/platform/layers/{env}/{agent_name}/s3-key")
    script_s3_key = get_ssm_param(ssm, f"/platform/agents/{env}/{agent_name}/script-s3-key")

    if not layer_hash or not layer_s3_key or not script_s3_key:
        logger.error(
            f"Deployment metadata not found for agent '{agent_name}' in env '{env}'. "
            "Run build_layer and deploy_agent first."
        )
        return False

    deployed_at = datetime.datetime.now(datetime.UTC).isoformat()
    # Default status: PENDING for prod (requires approval), RELEASED for others
    default_status = "pending" if env == "prod" else "released"
    runtime_arn = get_ssm_param(ssm, f"/platform/agents/{env}/{agent_name}/runtime-arn")

    body = {
        "agentName": agent_name,
        "version": manifest.version,
        "ownerTeam": manifest.owner_team,
        "tierMinimum": manifest.tier_minimum.value,
        "layerHash": layer_hash,
        "layerS3Key": layer_s3_key,
        "scriptS3Key": script_s3_key,
        "deployedAt": deployed_at,
        "invocationMode": manifest.invocation_mode.value,
        "streamingEnabled": manifest.streaming_enabled,
        "status": default_status,
        "runtimeArn": runtime_arn,
        "estimatedDurationSeconds": manifest.estimated_duration_seconds,
        "commitSha": os.environ.get("CI_COMMIT_SHA"),
        "pipelineUrl": os.environ.get("CI_PIPELINE_URL"),
        "jobId": os.environ.get("CI_JOB_ID"),
    }

    # Resolve API Base URL and Token
    api_url = api_base_url or os.environ.get("API_BASE_URL") or os.environ.get("VITE_API_BASE_URL")
    if not api_url:
        logger.error("API_BASE_URL environment variable is not set")
        return False

    api_token = (
        token or os.environ.get("PLATFORM_ACCESS_TOKEN") or os.environ.get("OPS_ACCESS_TOKEN")
    )
    if not api_token:
        # Try to load from local credentials if in dev
        creds_path = Path.home() / ".platform" / "credentials"
        if creds_path.exists():
            try:
                creds = json.loads(creds_path.read_text())
                profile = creds.get("profiles", {}).get(env, {})
                api_token = profile.get("accessToken")
                if not api_url:
                    api_url = profile.get("apiBaseUrl")
            except Exception:
                pass

    if not api_token:
        logger.error("PLATFORM_ACCESS_TOKEN environment variable is not set")
        return False

    register_url = f"{api_url.rstrip('/')}/v1/platform/agents/register"

    logger.info(f"Registering agent '{agent_name}' v{manifest.version} via API in {env}")
    try:
        _request_api(register_url, "POST", api_token, body)

        # Update latest-version in SSM if released (as a fallback/convenience for infra)
        if default_status == "released":
            ssm.put_parameter(
                Name=f"/platform/agents/{env}/{agent_name}/latest-version",
                Value=manifest.version,
                Type="String",
                Overwrite=True,
            )
    except Exception as e:
        logger.error(f"Registration failed: {e}")
        return False

    logger.info(f"Agent '{agent_name}' registered successfully via API")
    return True


if __name__ == "__main__":
    args = parse_args()
    if not register_agent(args.agent_name, args.env, args.api_base_url, args.token):
        sys.exit(1)
