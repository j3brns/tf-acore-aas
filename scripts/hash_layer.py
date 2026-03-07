"""
hash_layer.py — Dependency hash checker for agent layer caching.

Reads [project.dependencies] from the agent's pyproject.toml, computes a
canonical SHA256 hash, and compares against the stored hash in SSM.

Exit codes:
    0  Hash matches — dependencies unchanged, use warm push path (~15s)
    1  Hash mismatch — dependencies changed, rebuild required (~90s)

Hash algorithm:
    - Read [project.dependencies] list
    - Canonicalise: sort, strip whitespace
    - SHA256 of canonical form
    - First 16 hex characters

Usage:
    uv run python scripts/hash_layer.py <agent_name> --env <env>

Implemented in TASK-033.
ADRs: ADR-006, ADR-008
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import tomllib
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("hash_layer")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HASH_LENGTH = 16


def compute_dependency_hash(deps: list[str]) -> str:
    """Return a canonical SHA256 hash of a dependency list.

    Canonical form: each entry stripped of whitespace, sorted, joined by
    newline.  Returns the first HASH_LENGTH hex characters of SHA256.
    Same deps in any order produce the same hash.
    """
    canonical = "\n".join(sorted(d.strip() for d in deps))
    return hashlib.sha256(canonical.encode()).hexdigest()[:HASH_LENGTH]


def read_agent_deps(agent_name: str) -> list[str]:
    """Read [project.dependencies] from agents/{agent_name}/pyproject.toml."""
    toml_path = REPO_ROOT / "agents" / agent_name / "pyproject.toml"
    if not toml_path.exists():
        raise FileNotFoundError(f"pyproject.toml not found: {toml_path}")

    with toml_path.open("rb") as fh:
        data = tomllib.load(fh)

    deps = data.get("project", {}).get("dependencies", [])
    if not isinstance(deps, list):
        raise ValueError(f"[project.dependencies] must be a list in {toml_path}")
    return [str(d) for d in deps]


def get_ssm_hash(agent_name: str, aws_region: str) -> str | None:
    """Read stored hash from SSM /platform/layers/{agent_name}/hash.

    Returns None when the parameter does not exist yet (first build).
    """
    ssm = boto3.client("ssm", region_name=aws_region)
    param_name = f"/platform/layers/{agent_name}/hash"
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


def run(agent_name: str) -> int:
    """Run hash check.

    Returns:
        0  Hash matches — fast warm push path.
        1  Hash mismatch or parameter absent — rebuild required.
    """
    aws_region = require_aws_region()

    deps = read_agent_deps(agent_name)
    computed = compute_dependency_hash(deps)
    logger.info(
        "Computed hash for %s: %s (%d deps)",
        agent_name,
        computed,
        len(deps),
    )

    stored = get_ssm_hash(agent_name, aws_region)
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
        return run(agent_name=args.agent_name)
    except Exception as exc:
        logger.error("hash_layer failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
