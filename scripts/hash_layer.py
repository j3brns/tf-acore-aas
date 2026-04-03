"""
hash_layer.py — Dependency hash checker for agent layer caching.

Reads [project.dependencies] from the agent's pyproject.toml and the resolved
lockfile (uv.lock), computes a canonical SHA256 hash, and compares against the
stored hash in SSM.

Exit codes:
    0  Hash matches — dependencies unchanged, use warm push path (~15s)
    1  Hash mismatch — dependencies changed, rebuild required (~90s)

Hash algorithm:
    - Read [project.dependencies] list
    - Read uv.lock content (if present)
    - Canonicalise deps: sort, strip whitespace
    - Combine canonical deps with lockfile content
    - SHA256 of combined form
    - First 16 hex characters

Usage:
    uv run python scripts/hash_layer.py <agent_name> --env <env>

Implemented in TASK-033, updated in issue #267.
ADRs: ADR-006, ADR-008
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("hash_layer")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import layer_manifest  # noqa: E402

compute_dependency_hash = layer_manifest.compute_dependency_hash
read_agent_deps = layer_manifest.read_agent_deps
read_agent_lockfile = layer_manifest.read_agent_lockfile
read_deployment_type = layer_manifest.read_deployment_type


def get_ssm_hash(agent_name: str, env: str, aws_region: str) -> str | None:
    """Read stored hash from SSM /platform/layers/{env}/{agent_name}/hash.

    Returns None when the parameter does not exist yet (first build).
    """
    ssm = boto3.client("ssm", region_name=aws_region)
    param_name = f"/platform/layers/{env}/{agent_name}/hash"
    try:
        response = ssm.get_parameter(Name=param_name)
        value = response["Parameter"].get("Value")
        return str(value) if value else None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ParameterNotFound":
            logger.info("SSM parameter not found: %s", param_name)
            return None
        raise


def require_aws_region() -> str:
    """Read AWS_REGION from environment and fail fast if missing."""
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION must be set")
    return region


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Check agent dependency hash against SSM stored value"
    )
    parser.add_argument(
        "agent_name",
        help="Agent name (must match agents/<agent_name>/pyproject.toml)",
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=["dev", "staging", "prod"],
        help="Target environment",
    )
    return parser.parse_args(argv)


def run(agent_name: str, env: str) -> int:
    """Run hash check.

    Returns:
        0  Hash matches — fast warm push path.
        1  Hash mismatch or parameter absent — rebuild required.
    """
    if read_deployment_type(agent_name) == "container":
        logger.info("Skipping layer hash check for container deployment: %s", agent_name)
        print(f"HASH_MATCH deployment=container agent={agent_name}")
        return 0

    aws_region = require_aws_region()

    deps = read_agent_deps(agent_name)
    lockfile = read_agent_lockfile(agent_name)
    computed = compute_dependency_hash(deps, lockfile_content=lockfile)
    logger.info(
        "Computed hash for %s: %s (%d deps, lockfile=%s)",
        agent_name,
        computed,
        len(deps),
        "present" if lockfile is not None else "absent",
    )

    stored = get_ssm_hash(agent_name, env, aws_region)
    if stored is None:
        logger.info("No stored hash — rebuild required")
        print(f"HASH_MISMATCH computed={computed} stored=none")
        return 1

    if computed == stored:
        logger.info("Hash match — warm path")
        print(f"HASH_MATCH hash={computed}")
        return 0

    logger.info(
        "Hash mismatch — rebuild required (computed=%s stored=%s)",
        computed,
        stored,
    )
    print(f"HASH_MISMATCH computed={computed} stored={stored}")
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    try:
        return run(agent_name=args.agent_name, env=args.env)
    except Exception as exc:
        logger.error("hash_layer failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
