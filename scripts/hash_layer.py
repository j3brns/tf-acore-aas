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


def compute_dependency_hash(deps: list[str], lockfile_content: str | None = None) -> str:
    """Return a canonical SHA256 hash of dependencies and lockfile state.

    Canonical form: each dep entry stripped of whitespace, sorted, joined by
    newline.  When lockfile content is provided it is appended after a separator.
    Returns the first HASH_LENGTH hex characters of SHA256.
    Same deps in any order produce the same hash.
    """
    canonical = "\n".join(sorted(d.strip() for d in deps))
    if lockfile_content is not None:
        canonical = canonical + "\n---lockfile---\n" + lockfile_content
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


def read_agent_lockfile(agent_name: str) -> str | None:
    """Read uv.lock from agents/{agent_name}/uv.lock if it exists.

    Returns the file content as a string, or None if the lockfile is absent.
    """
    lock_path = REPO_ROOT / "agents" / agent_name / "uv.lock"
    if not lock_path.exists():
        return None
    return lock_path.read_text(encoding="utf-8")


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
