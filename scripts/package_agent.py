"""package_agent.py — Package agent code for deployment.

Zips agent source code excluding: __pycache__, .venv, tests/, *.pyc, .git.
Output: .build/{agent_name}-code.zip

Usage:
    uv run python scripts/package_agent.py <agent_name>

Implemented in TASK-035.
ADRs: ADR-005, ADR-008
"""

import os
import sys
import zipfile
from pathlib import Path


def package_agent(agent_name: str, repo_root: Path | None = None) -> None:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[1]
    agent_dir = repo_root / "agents" / agent_name

    if not agent_dir.exists() or not agent_dir.is_dir():
        print(f"Error: Agent directory not found at {agent_dir}")
        sys.exit(1)

    build_dir = repo_root / ".build"
    build_dir.mkdir(exist_ok=True)

    zip_path = build_dir / f"{agent_name}-code.zip"
    print(f"Packaging {agent_name} to {zip_path}...")

    exclude_dirs = {"__pycache__", ".venv", "tests", ".git"}
    exclude_files = {".pyc", ".pyo", ".ds_store"}

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # We want the agent directory contents to be at the root of the zip
        for root, dirs, files in os.walk(agent_dir):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in exclude_dirs]

            for file in files:
                if any(file.lower().endswith(ext) for ext in exclude_files):
                    continue

                file_path = Path(root) / file
                arcname = file_path.relative_to(agent_dir)
                zf.write(file_path, arcname)

    print(f"Successfully packaged {agent_name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/package_agent.py <agent_name>")
        sys.exit(1)

    package_agent(sys.argv[1])
