#!/usr/bin/env python3
"""
check_gitlab_protected_environment.py — Fail closed unless prod deploy approval is real.

Audits the GitLab Protected Environments API before a production deployment starts.
This replaces mutable string sentinels with a machine-checkable control.

Required CI variables:
  CI_API_V4_URL
  CI_PROJECT_ID
  GITLAB_PROTECTED_ENV_API_TOKEN  (protected + masked, scope: read_api)

Usage:
    uv run python scripts/check_gitlab_protected_environment.py
    uv run python scripts/check_gitlab_protected_environment.py \
      --environment prod \
      --min-approvals 2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_ENVIRONMENT_NAME = "prod"
DEFAULT_MIN_APPROVALS = 2
DEFAULT_TIMEOUT_SECONDS = 10.0


class ProtectionCheckError(RuntimeError):
    """Raised when the GitLab protected-environment control cannot be verified."""


@dataclass(frozen=True)
class ProtectedEnvironmentCheck:
    environment_name: str
    required_approval_count: int
    approval_rule_count: int
    deploy_access_level_count: int


ProtectedEnvironmentFetcher = Callable[[str, str, str, str, float], dict[str, Any]]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", help="GitLab API base URL (defaults to CI_API_V4_URL)")
    parser.add_argument("--project-id", help="GitLab project ID (defaults to CI_PROJECT_ID)")
    parser.add_argument(
        "--environment",
        default=DEFAULT_ENVIRONMENT_NAME,
        help=f"Protected environment name to verify (default: {DEFAULT_ENVIRONMENT_NAME})",
    )
    parser.add_argument(
        "--min-approvals",
        type=int,
        default=DEFAULT_MIN_APPROVALS,
        help=f"Minimum required approvals (default: {DEFAULT_MIN_APPROVALS})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    return parser.parse_args(argv)


def require_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ProtectionCheckError(f"Missing required environment variable: {name}")
    return value


def build_api_url(api_url: str, project_id: str, environment_name: str) -> str:
    project_ref = quote(project_id, safe="")
    env_ref = quote(environment_name, safe="")
    return f"{api_url.rstrip('/')}/projects/{project_ref}/protected_environments/{env_ref}"


def _coerce_non_negative_int(value: object, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ProtectionCheckError(f"{field_name} must be an integer, got boolean")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.isdigit():
        result = int(value)
    else:
        raise ProtectionCheckError(f"{field_name} must be an integer, got {value!r}")
    if result < 0:
        raise ProtectionCheckError(f"{field_name} must be non-negative, got {result}")
    return result


def derive_required_approval_count(payload: Mapping[str, Any]) -> int:
    top_level = _coerce_non_negative_int(
        payload.get("required_approval_count"),
        field_name="required_approval_count",
    )
    if top_level is not None:
        return top_level

    approval_rules = payload.get("approval_rules")
    if not isinstance(approval_rules, list) or not approval_rules:
        raise ProtectionCheckError(
            "GitLab protected environment response is missing approval metadata"
        )

    total = 0
    for index, rule in enumerate(approval_rules):
        if not isinstance(rule, Mapping):
            raise ProtectionCheckError(f"approval_rules[{index}] must be an object")
        approvals = _coerce_non_negative_int(
            rule.get("required_approvals"),
            field_name=f"approval_rules[{index}].required_approvals",
        )
        total += approvals or 0
    return total


def validate_protected_environment(
    payload: Mapping[str, Any],
    *,
    expected_environment_name: str,
    min_approvals: int,
) -> ProtectedEnvironmentCheck:
    actual_environment_name = str(payload.get("name", "")).strip()
    if actual_environment_name != expected_environment_name:
        raise ProtectionCheckError(
            "GitLab protected environment name mismatch: "
            f"expected {expected_environment_name!r}, got {actual_environment_name!r}"
        )

    required_approval_count = derive_required_approval_count(payload)
    if required_approval_count < min_approvals:
        raise ProtectionCheckError(
            f"Protected environment {expected_environment_name!r} requires "
            f"{required_approval_count} approvals; expected at least {min_approvals}"
        )

    approval_rules = payload.get("approval_rules")
    approval_rule_count = len(approval_rules) if isinstance(approval_rules, list) else 0

    deploy_access_levels = payload.get("deploy_access_levels")
    deploy_access_level_count = (
        len(deploy_access_levels) if isinstance(deploy_access_levels, list) else 0
    )

    return ProtectedEnvironmentCheck(
        environment_name=actual_environment_name,
        required_approval_count=required_approval_count,
        approval_rule_count=approval_rule_count,
        deploy_access_level_count=deploy_access_level_count,
    )


def fetch_protected_environment(
    api_url: str,
    project_id: str,
    environment_name: str,
    api_token: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    request = Request(
        build_api_url(api_url, project_id, environment_name),
        headers={
            "Accept": "application/json",
            "PRIVATE-TOKEN": api_token,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            data = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            raise ProtectionCheckError(
                f"Protected environment {environment_name!r} was not found in project "
                f"{project_id}. Configure GitLab protected-environment approvals first."
            ) from exc
        if exc.code in {401, 403}:
            raise ProtectionCheckError(
                "GitLab API token cannot read protected environments. Confirm "
                "GITLAB_PROTECTED_ENV_API_TOKEN is protected, masked, and has read_api scope."
            ) from exc
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise ProtectionCheckError(
            f"GitLab protected environment API returned HTTP {exc.code}: {detail or 'no body'}"
        ) from exc
    except URLError as exc:
        raise ProtectionCheckError(f"Unable to reach GitLab API: {exc.reason}") from exc

    if not isinstance(data, dict):
        raise ProtectionCheckError("GitLab protected environment API returned a non-object JSON")
    return data


def run(
    *,
    api_url: str | None = None,
    project_id: str | None = None,
    environment_name: str = DEFAULT_ENVIRONMENT_NAME,
    min_approvals: int = DEFAULT_MIN_APPROVALS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
    fetcher: ProtectedEnvironmentFetcher = fetch_protected_environment,
) -> ProtectedEnvironmentCheck:
    if min_approvals < 1:
        raise ProtectionCheckError("min_approvals must be at least 1")

    env_map = env if env is not None else os.environ
    resolved_api_url = api_url or require_env(env_map, "CI_API_V4_URL")
    resolved_project_id = project_id or require_env(env_map, "CI_PROJECT_ID")
    api_token = require_env(env_map, "GITLAB_PROTECTED_ENV_API_TOKEN")

    payload = fetcher(
        resolved_api_url,
        resolved_project_id,
        environment_name,
        api_token,
        timeout_seconds,
    )
    return validate_protected_environment(
        payload,
        expected_environment_name=environment_name,
        min_approvals=min_approvals,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(
            api_url=args.api_url,
            project_id=args.project_id,
            environment_name=args.environment,
            min_approvals=args.min_approvals,
            timeout_seconds=args.timeout_seconds,
        )
    except ProtectionCheckError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        "Protected environment verified: "
        f"name={result.environment_name} "
        f"required_approvals={result.required_approval_count} "
        f"approval_rules={result.approval_rule_count} "
        f"deploy_access_levels={result.deploy_access_level_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
