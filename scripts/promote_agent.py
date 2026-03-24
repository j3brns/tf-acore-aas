"""
promote_agent.py — Promote an agent version to RELEASED status using Platform API.

Usage:
    uv run python scripts/promote_agent.py <agent_name> <version> --env <env>
    [--notes "Release notes"] [--score 0.95] [--report-url "http://..."]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("promote_agent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


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
    parser.add_argument("--score", type=float, help="Evaluation score")
    parser.add_argument("--report-url", help="Evaluation report URL")
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


def promote_agent(
    agent_name: str,
    version: str,
    env: str,
    notes: str | None,
    score: float | None,
    report_url: str | None,
    api_base_url: str | None,
    token: str | None,
) -> bool:
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

    promote_url = f"{api_url.rstrip('/')}/v1/platform/agents/{agent_name}/versions/{version}"

    body: dict[str, Any] = {"status": "released"}
    if notes:
        body["releaseNotes"] = notes
    if score is not None:
        body["evaluationScore"] = score
    if report_url:
        body["evaluationReportUrl"] = report_url

    logger.info(f"Promoting agent '{agent_name}' v{version} via API in {env}")
    try:
        _request_api(promote_url, "PATCH", api_token, body)

        # Update latest-version in SSM (as a fallback/convenience for infra)
        ssm = boto3.client("ssm", region_name=aws_region)
        ssm.put_parameter(
            Name=f"/platform/agents/{env}/{agent_name}/latest-version",
            Value=version,
            Type="String",
            Overwrite=True,
        )
    except Exception as e:
        logger.error(f"Promotion failed: {e}")
        return False

    logger.info(f"Agent '{agent_name}' v{version} promoted successfully via API")
    return True


if __name__ == "__main__":
    args = parse_args()
    if not promote_agent(
        args.agent_name,
        args.version,
        args.env,
        args.notes,
        args.score,
        args.report_url,
        args.api_base_url,
        args.token,
    ):
        sys.exit(1)
