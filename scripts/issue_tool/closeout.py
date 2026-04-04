from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from scripts.issue_tool.constants import WORKTREE_BRANCH_REGEX, WORKTREE_CLOSEOUT_DIR
from scripts.issue_tool.git_utils import run
from scripts.issue_tool.models import WorktreeInfo


def cleanup_finished_worktree(
    root: Path,
    target: WorktreeInfo,
    *,
    local_branch_exists_fn: Callable[[Path, str], bool],
    os_module=os,
    run_fn=run,
) -> dict[str, bool]:
    result = {
        "worktree_removed": False,
        "branch_deleted": False,
        "worktree_pruned": False,
    }
    branch = target.branch
    print("Cleaning up worktree...")
    try:
        cwd = Path(os_module.getcwd()).resolve()
    except FileNotFoundError:
        cwd = None
    if cwd is None or cwd == target.path.resolve() or target.path.resolve() in cwd.parents:
        os_module.chdir(root)
    if target.path.exists():
        run_fn(["git", "worktree", "remove", str(target.path)], cwd=root)
        print(f"Removed worktree {target.path}")
        result["worktree_removed"] = True
    else:
        print(f"Worktree path missing, skipping remove: {target.path}")
    if branch and branch != "(detached)" and WORKTREE_BRANCH_REGEX.fullmatch(branch):
        if local_branch_exists_fn(root, branch):
            run_fn(["git", "branch", "-d", branch], cwd=root)
            print(f"Deleted branch {branch}")
            result["branch_deleted"] = True
        else:
            print(f"Branch already absent, skipping delete: {branch}")
    run_fn(["git", "worktree", "prune"], cwd=root)
    print("Pruned stale worktree refs")
    result["worktree_pruned"] = True
    return result


def closeout_report_path(
    root: Path,
    target: WorktreeInfo,
    *,
    extract_issue_id_from_branch_fn: Callable[[str], int | None],
) -> Path:
    issue_id = extract_issue_id_from_branch_fn(target.branch) or "unknown"
    safe_branch = re.sub(r"[^A-Za-z0-9._-]+", "_", target.branch)
    return root / WORKTREE_CLOSEOUT_DIR / f"issue-{issue_id}-{safe_branch}.json"


def write_closeout_report(
    root: Path,
    target: WorktreeInfo,
    payload: dict[str, object],
    *,
    extract_issue_id_from_branch_fn: Callable[[str], int | None],
) -> Path:
    path = closeout_report_path(
        root, target, extract_issue_id_from_branch_fn=extract_issue_id_from_branch_fn
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "branch": target.branch,
        "generated_at": datetime.now(UTC).isoformat(),
        "worktree_path": str(target.path),
        **payload,
    }
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_closeout_report(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def closeout_event(
    *,
    stage: str,
    message: str,
    target: WorktreeInfo,
    repo: str | None,
    issue_id: int | None,
) -> dict[str, object]:
    return {
        "ts": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "stage": stage,
        "message": message,
        "branch": target.branch,
        "worktree_path": str(target.path),
        "repo": repo,
        "issue_id": issue_id,
    }


def verify_cleanup_finished(
    root: Path,
    target: WorktreeInfo,
    *,
    list_worktrees_fn: Callable[[Path], list[WorktreeInfo]],
    local_branch_exists_fn: Callable[[Path, str], bool],
) -> list[str]:
    issues: list[str] = []
    current_worktrees = list_worktrees_fn(root)
    if any(
        wt.path.resolve() == target.path.resolve() for wt in current_worktrees if wt.path.exists()
    ):
        issues.append(f"worktree still registered: {target.path}")
    if target.path.exists():
        issues.append(f"worktree path still exists: {target.path}")
    if (
        target.branch
        and target.branch != "(detached)"
        and WORKTREE_BRANCH_REGEX.fullmatch(target.branch)
    ):
        if local_branch_exists_fn(root, target.branch):
            issues.append(f"local branch still exists: {target.branch}")
    return issues
