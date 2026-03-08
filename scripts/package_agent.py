"""
package_agent.py — Package agent code for deployment.

Zips agent source code excluding: __pycache__, .venv, tests/, *.pyc, .git.
Output: .build/{agent_name}-code.zip

Usage:
    uv run python scripts/package_agent.py <agent_name>

Implemented in TASK-035.
ADRs: ADR-005, ADR-008
"""

from __future__ import annotations

import argparse
import logging
import zipfile
from pathlib import Path

logger = logging.getLogger("package_agent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

_REPO_ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_PATTERNS = {
    "__pycache__",
    ".venv",
    "tests",
    "*.pyc",
    ".git",
    ".build",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package agent code")
    parser.add_argument("agent_name", help="Name of the agent directory")
    return parser.parse_args()


def should_exclude(path: Path, base_dir: Path) -> bool:
    rel_path = path.relative_to(base_dir)
    for part in rel_path.parts:
        if part in EXCLUDE_PATTERNS:
            return True
    if path.suffix == ".pyc":
        return True
    return False


def package_agent(agent_name: str, repo_root: Path | None = None) -> bool:
    root = repo_root or _REPO_ROOT
    agent_dir = root / "agents" / agent_name
    if not agent_dir.exists():
        logger.error(f"Agent directory not found: {agent_dir}")
        return False

    build_dir = root / ".build"
    build_dir.mkdir(exist_ok=True)
    zip_path = build_dir / f"{agent_name}-code.zip"

    logger.info(f"Packaging agent '{agent_name}' to {zip_path}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in agent_dir.rglob("*"):
            if path.is_file() and not should_exclude(path, agent_dir):
                arcname = path.relative_to(agent_dir)
                zf.write(path, arcname)
                logger.debug(f"Added {arcname}")

    logger.info(f"Successfully packaged '{agent_name}' ({zip_path.stat().st_size} bytes)")
    return True


if __name__ == "__main__":
    args = parse_args()
    if not package_agent(args.agent_name):
        import sys

        sys.exit(1)
