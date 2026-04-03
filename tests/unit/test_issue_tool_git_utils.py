from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.issue_tool import git_utils


def test_repo_root_prefers_git_common_dir_parent(monkeypatch) -> None:
    root = Path("/tmp/repo")

    def _run(cmd, **_kwargs):
        if cmd == ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]:
            return subprocess.CompletedProcess(cmd, 0, str(root / ".git"), "")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(git_utils, "run", _run)

    assert git_utils.repo_root() == root
