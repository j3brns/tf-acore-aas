from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_worktree_module():
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "worktree_issues", repo_root / "scripts" / "worktree_issues.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


worktree_issues = _load_worktree_module()


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


def test_build_queue_auto_excludes_in_progress_from_candidates():
    in_progress = _issue(
        number=22,
        task_id="TASK-015",
        seq=150,
        labels=["type:task", "status:in-progress"],
    )
    next_not_started = _issue(
        number=23,
        task_id="TASK-016",
        seq=160,
        labels=["type:task", "status:not-started"],
    )

    selection = worktree_issues.build_queue([in_progress, next_not_started], mode="auto")

    assert selection.source_mode == "open-task"
    assert "excludes status:in-progress" in selection.source_note
    assert [item.issue.number for item in selection.items] == [23]


def test_choose_next_runnable_requires_not_blocked_and_dependencies_closed():
    blocked_by_label = _issue(
        number=23,
        task_id="TASK-016",
        seq=160,
        labels=["type:task", "status:blocked"],
    )
    blocked_by_dep = _issue(
        number=24,
        task_id="TASK-017",
        seq=170,
        labels=["type:task", "status:not-started"],
        depends_on=["TASK-099"],
    )
    closed_dependency = _issue(
        number=25,
        task_id="TASK-018",
        seq=180,
        state="closed",
        labels=["type:task", "status:done"],
    )
    runnable = _issue(
        number=26,
        task_id="TASK-019",
        seq=190,
        labels=["type:task", "status:not-started"],
        depends_on=["TASK-018"],
    )

    selection = worktree_issues.build_queue(
        [blocked_by_label, blocked_by_dep, closed_dependency, runnable], mode="open-task"
    )

    next_item = worktree_issues.choose_next_runnable(selection)
    assert next_item.issue.number == 26


def test_audit_issues_flags_invalid_status_and_ready_combinations():
    closed_wrong_status = _issue(
        number=30,
        task_id="TASK-023",
        seq=230,
        state="closed",
        labels=["type:task", "status:in-progress"],
    )
    open_done = _issue(
        number=31,
        task_id="TASK-024",
        seq=240,
        state="open",
        labels=["type:task", "status:done"],
    )
    ready_in_progress = _issue(
        number=32,
        task_id="TASK-025",
        seq=250,
        state="open",
        labels=["type:task", "status:in-progress", "ready"],
    )

    findings = worktree_issues.audit_issues([closed_wrong_status, open_done, ready_in_progress])
    messages = [f.message for f in findings if f.severity == "error"]

    assert any("closed task must be status:done" in msg for msg in messages)
    assert any("open task cannot be status:done" in msg for msg in messages)
    assert any("ready label requires status:not-started" in msg for msg in messages)


def test_audit_issues_passes_clean_state_with_next_startable():
    in_progress = _issue(
        number=22,
        task_id="TASK-015",
        seq=150,
        labels=["type:task", "status:in-progress"],
    )
    next_not_started = _issue(
        number=23,
        task_id="TASK-016",
        seq=160,
        labels=["type:task", "status:not-started"],
    )
    done = _issue(
        number=21,
        task_id="TASK-014",
        seq=140,
        state="closed",
        labels=["type:task", "status:done"],
    )

    findings = worktree_issues.audit_issues([in_progress, next_not_started, done])
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    assert errors == []
    assert warnings == []


def test_reconcile_issue_label_changes_closed_in_progress_moves_to_done():
    issue = _issue(
        number=40,
        task_id="TASK-040",
        seq=400,
        state="closed",
        labels=["type:task", "status:in-progress", "ready"],
    )
    add_labels, remove_labels = worktree_issues.reconcile_issue_label_changes(issue)
    assert add_labels == ["status:done"]
    assert set(remove_labels) == {"ready", "status:in-progress"}


def test_assert_issue_startable_rejects_in_progress():
    issue = _issue(
        number=41,
        task_id="TASK-041",
        seq=410,
        labels=["type:task", "status:in-progress"],
    )
    with pytest.raises(worktree_issues.CliError, match="already status:in-progress"):
        worktree_issues.assert_issue_startable(issue, allow_blocked=False)
