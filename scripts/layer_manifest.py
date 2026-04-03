"""Shared manifest and hash helpers for agent dependency layers."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HASH_LENGTH = 16


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
    return [str(dep) for dep in deps]


def read_deployment_type(agent_name: str) -> str:
    """Read deployment.type from agents/{agent_name}/pyproject.toml."""
    toml_path = REPO_ROOT / "agents" / agent_name / "pyproject.toml"
    if not toml_path.exists():
        raise FileNotFoundError(f"pyproject.toml not found: {toml_path}")

    with toml_path.open("rb") as fh:
        data = tomllib.load(fh)

    deployment = data.get("tool", {}).get("agentcore", {}).get("deployment", {})
    deployment_type = deployment.get("type", "zip")
    if not isinstance(deployment_type, str):
        raise ValueError(f"[tool.agentcore.deployment.type] must be a string in {toml_path}")
    return deployment_type


def read_agent_lockfile(agent_name: str) -> str | None:
    """Read uv.lock from agents/{agent_name}/uv.lock if it exists."""
    lock_path = REPO_ROOT / "agents" / agent_name / "uv.lock"
    if not lock_path.exists():
        return None
    return lock_path.read_text(encoding="utf-8")


def compute_dependency_hash(deps: list[str], lockfile_content: str | None = None) -> str:
    """Return canonical dependency hash used for S3 key and SSM metadata."""
    canonical = "\n".join(sorted(dep.strip() for dep in deps))
    if lockfile_content is not None:
        canonical = canonical + "\n---lockfile---\n" + lockfile_content
    return hashlib.sha256(canonical.encode()).hexdigest()[:HASH_LENGTH]
