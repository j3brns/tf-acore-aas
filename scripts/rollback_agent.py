"""
rollback_agent.py — Roll back an agent version using the Platform API.

Uses the Platform API (tenant-api) to mark the current version as ROLLBACK.
The Bridge Lambda automatically falls back to the previous RELEASED version.

Usage:
    uv run python scripts/rollback_agent.py <agent_name> --env <env>
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3

logger = logging.getLogger("rollback_agent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def require_aws_region() -> str:
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION must be set")
    return region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roll back agent")
    parser.add_argument("agent_name", help="Name of the agent")
    parser.add_argument("--env", required=True, choices=["dev", "staging", "prod"])
    parser.add_argument("--api-base-url", help="Override Platform API base URL")
    parser.add_argument("--token", help="Override Platform API access token")
    return parser.parse_args()


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


def rollback_agent(agent_name: str, env: str, api_base_url: str | None, token: str | None) -> bool:
    aws_region = require_aws_region()

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

    # 1. Get current released version
    agents_url = f"{api_url.rstrip('/')}/v1/platform/agents"
    logger.info(f"Fetching agent versions for '{agent_name}'")
    try:
        resp = _request_api(agents_url, "GET", api_token)
        items = resp.get("items", [])
        agent_versions = [
            i for i in items if i.get("agent_name") == agent_name and i.get("status") == "released"
        ]
        if not agent_versions:
            logger.error(f"No RELEASED versions found for agent '{agent_name}'")
            return False

        # Sort by semver (naive)
        agent_versions.sort(key=lambda x: [int(p) for p in x["version"].split(".")], reverse=True)
        current_version = agent_versions[0]["version"]

        if len(agent_versions) < 2:
            logger.error(
                f"Rollback failed: No previous RELEASED version found for agent '{agent_name}'. "
                f"Current version is {current_version}."
            )
            return False
    except Exception as e:
        logger.error(f"Failed to fetch agent versions: {e}")
        return False

    rollback_url = (
        f"{api_url.rstrip('/')}/v1/platform/agents/{agent_name}/versions/{current_version}"
    )
    body = {"status": "rollback"}

    logger.info(f"Rolling back agent '{agent_name}' v{current_version} via API in {env}")
    try:
        _request_api(rollback_url, "PATCH", api_token, body)

        # 2. Update latest-version in SSM (as a fallback/convenience for infra)
        # We need to find the NEW latest released version
        if len(agent_versions) > 1:
            new_version = agent_versions[1]["version"]
            ssm = boto3.client("ssm", region_name=aws_region)
            ssm.put_parameter(
                Name=f"/platform/agents/{env}/{agent_name}/latest-version",
                Value=new_version,
                Type="String",
                Overwrite=True,
            )
            logger.info(f"Updated SSM latest-version to v{new_version}")
    except Exception as e:
        logger.error(f"Rollback failed: {e}")
        return False

    logger.info(f"Agent '{agent_name}' v{current_version} rolled back successfully via API")
    return True


if __name__ == "__main__":
    args = parse_args()
    if not rollback_agent(args.agent_name, args.env, args.api_base_url, args.token):
        sys.exit(1)
