from __future__ import annotations

import argparse
import importlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

worktree_issues = importlib.import_module("scripts.issue_tool.cli")

__all__ = [
    "argparse",
    "json",
    "os",
    "signal",
    "subprocess",
    "sys",
    "tempfile",
    "Path",
    "pytest",
    "worktree_issues",
    "_issue",
]


def _issue(
    *,
    number: int,
    task_id: str,
    seq: int,
    state: str = "open",
    labels: list[str] | None = None,
    depends_on: list[str] | None = None,
):
    return worktree_issues.Issue(
        number=number,
        title=f"{task_id}: Test issue {number}",
        state=state,
        created_at="2026-01-01T00:00:00Z",
        body=f"Seq: {seq}\nDepends on: none",
        labels=labels or ["type:task", "status:not-started"],
        url=f"https://example.test/issues/{number}",
        task_id=task_id,
        seq=seq,
        depends_on=depends_on or [],
    )
