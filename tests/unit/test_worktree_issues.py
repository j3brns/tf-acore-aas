from __future__ import annotations

import argparse
import importlib.util
import subprocess
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


def test_cmd_worktree_resume_open_shell_tolerates_missing_agent_namespace_attrs(monkeypatch):
    root = Path("/tmp/repo")
    wt = worktree_issues.WorktreeInfo(
        path=Path("/tmp/worktrees/wt33"),
        head="abc123",
        branch="wt/infra/33-observabilitystack",
        is_primary=False,
    )
    called: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "list_resume_candidates", lambda _root: [wt])
    monkeypatch.setattr(worktree_issues, "select_worktree_interactive", lambda items: wt)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(worktree_issues, "run_preflight", lambda **kwargs: None)

    def _handoff(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(worktree_issues, "handoff_to_agent_or_shell", _handoff)

    args = argparse.Namespace(
        path=None,
        no_preflight=False,
        open_shell=True,
        command=None,
    )
    rc = worktree_issues.cmd_worktree_resume(args)

    assert rc == 0
    assert called["path"] == wt.path
    assert called["agent"] is None
    assert called["agent_mode"] is None
    assert called["handoff"] is None
    assert called["print_only_override"] is False


def test_cmd_worktree_next_skips_runnable_issue_with_existing_worktree(monkeypatch):
    root = Path("/tmp/repo")
    repo = "owner/repo"
    issue_33 = _issue(
        number=33,
        task_id="TASK-026",
        seq=260,
        labels=["type:task", "status:not-started", "ready"],
    )
    issue_35 = _issue(
        number=35,
        task_id="TASK-028",
        seq=280,
        labels=["type:task", "status:not-started", "ready"],
    )
    created: dict[str, object] = {}
    existing = worktree_issues.WorktreeInfo(
        path=Path("/tmp/worktrees/wt33"),
        head="abc123",
        branch="wt/infra/33-observabilitystack",
        is_primary=False,
    )

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(
        worktree_issues,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue_33, issue_35],
    )
    monkeypatch.setattr(worktree_issues, "list_resume_candidates", lambda _root: [existing])

    def _create(**kwargs):
        created.update(kwargs)
        return Path("/tmp/worktrees/wt35")

    monkeypatch.setattr(worktree_issues, "create_worktree_for_issue", _create)

    args = argparse.Namespace(
        repo=None,
        stream_label=None,
        mode="auto",
        choose=False,
        allow_blocked=False,
        base_dir=None,
        base_ref=None,
        scope=None,
        slug=None,
        name=None,
        no_claim=False,
        no_preflight=True,
        dry_run=True,
        open_shell=False,
        agent=None,
        agent_mode=None,
        handoff=None,
        print_only=False,
    )
    rc = worktree_issues.cmd_worktree_next(args)

    assert rc == 0
    selected_issue = created["issue"]
    assert isinstance(selected_issue, worktree_issues.Issue)
    assert selected_issue.number == 35


def test_create_worktree_for_issue_attaches_existing_local_branch(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    base_dir = tmp_path / "worktrees"
    issue = _issue(
        number=25,
        task_id="TASK-018",
        seq=180,
        labels=["type:task", "status:not-started"],
    )
    executed: list[list[str]] = []

    def _run(cmd, **_kwargs):
        executed.append(cmd)
        if cmd[:3] == ["git", "show-ref", "--verify"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "worktree", "add"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(worktree_issues, "run", _run)
    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda _path: None)

    wt_path = worktree_issues.create_worktree_for_issue(
        root=root,
        repo="owner/repo",
        issue=issue,
        base_dir=base_dir,
        base_ref=None,
        scope="task",
        slug="write-src-bridge-handler-py",
        folder_name="wt25",
        auto_claim=False,
        preflight=False,
        dry_run=False,
    )

    assert wt_path == (base_dir / "wt25").resolve()
    assert [
        "git",
        "worktree",
        "add",
        str(wt_path),
        "wt/task/25-write-src-bridge-handler-py",
    ] in executed


def test_build_agent_prompt_for_worktree_includes_explicit_dod_and_conflict_requirements(
    monkeypatch, tmp_path
):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    wt = tmp_path / "worktrees" / "wt53"
    wt.mkdir(parents=True, exist_ok=True)

    def _run_prompt(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "wt/infra/53-explicit-dod\n", "")

    monkeypatch.setattr(worktree_issues, "run", _run_prompt)
    monkeypatch.setattr(worktree_issues, "worktree_issue_id", lambda _path: 53)
    monkeypatch.setattr(
        worktree_issues, "fetch_issue_labels_for_prompt", lambda _root, _repo, _issue: "type:task"
    )

    prompt = worktree_issues.build_agent_prompt_for_worktree(wt, root, "owner/repo")

    assert "DoD: do not stop at code-complete." in prompt
    assert "Merge conflicts are resolved and re-validated." in prompt
    assert "make finish-worktree-close" in prompt
    assert "git worktree prune" in prompt


def test_finish_summary_prints_explicit_dod_conflict_and_cleanup_steps(monkeypatch, capsys):
    root = Path("/tmp/repo")
    primary = worktree_issues.WorktreeInfo(
        path=Path("/tmp/repo"),
        head="abc123",
        branch="main",
        is_primary=True,
    )
    target = worktree_issues.WorktreeInfo(
        path=Path("/tmp/worktrees/wt53"),
        head="def456",
        branch="wt/infra/53-explicit-dod",
        is_primary=False,
    )

    monkeypatch.setattr(worktree_issues, "list_worktrees", lambda _root: [primary, target])
    monkeypatch.setattr(worktree_issues, "resolve_current_worktree", lambda _path, _wts: target)
    monkeypatch.setattr(worktree_issues, "current_path", lambda: target.path)
    monkeypatch.setattr(worktree_issues, "gh_repo_ready", lambda _root: (False, None))
    monkeypatch.setattr(worktree_issues, "finish_stage", lambda *_args, **_kwargs: "merged")

    def _run_summary(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "## wt/infra/53-explicit-dod\n", "")

    monkeypatch.setattr(worktree_issues, "run", _run_summary)

    worktree_issues.finish_summary(root, path=target.path)
    out = capsys.readouterr().out

    assert "dod:      merged PR + closed issue + cleaned worktree/branch" in out
    assert "next:     make finish-worktree-close" in out
    assert "conflict: if merge/rebase conflicts appear:" in out
    assert "cleanup:  git worktree remove <this-worktree-path>" in out
    assert "git worktree prune" in out


def test_close_issue_done_normalizes_labels_for_already_closed_issue(monkeypatch, capsys):
    root = Path("/tmp/repo")
    target = worktree_issues.WorktreeInfo(
        path=Path("/tmp/worktrees/wt153"),
        head="abc123",
        branch="wt/task/153-sample",
        is_primary=False,
    )
    primary = worktree_issues.WorktreeInfo(
        path=root,
        head="def456",
        branch="main",
        is_primary=True,
    )
    edits: list[list[str]] = []

    monkeypatch.setattr(worktree_issues, "list_worktrees", lambda _root: [primary, target])
    monkeypatch.setattr(worktree_issues, "resolve_current_worktree", lambda _path, _wts: target)
    monkeypatch.setattr(worktree_issues, "current_path", lambda: target.path)
    monkeypatch.setattr(worktree_issues, "gh_repo_ready", lambda _root: (True, "owner/repo"))
    monkeypatch.setattr(
        worktree_issues,
        "pr_for_branch",
        lambda _root, _repo, _branch, _state: {"number": 157},
    )
    monkeypatch.setattr(
        worktree_issues,
        "issue_state_info",
        lambda _root, _repo, _issue_id: {
            "state": "CLOSED",
            "title": "TASK-153: sample",
            "url": "https://example.test/issues/153",
            "labels": [
                {"name": "type:task"},
                {"name": "status:in-progress"},
                {"name": "ready"},
            ],
        },
    )

    def _gh_text(args, *, root):
        edits.append(args)
        return ""

    monkeypatch.setattr(worktree_issues, "gh_text", _gh_text)
    monkeypatch.setattr(worktree_issues, "ensure_label_exists", lambda *_args, **_kwargs: None)

    worktree_issues.close_issue_done(root, path=target.path, force=False)
    out = capsys.readouterr().out

    assert edits == [
        [
            "issue",
            "edit",
            "153",
            "-R",
            "owner/repo",
            "--add-label",
            "status:done",
            "--remove-label",
            "ready",
            "--remove-label",
            "status:in-progress",
        ]
    ]
    assert "Issue #153 already closed." in out
    assert "Normalized closed-issue lifecycle labels." in out
