from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
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


def test_build_queue_can_start_from_issue_number():
    lower = _issue(
        number=22,
        task_id="TASK-015",
        seq=150,
        labels=["type:task", "status:not-started"],
    )
    higher = _issue(
        number=23,
        task_id="TASK-016",
        seq=160,
        labels=["type:task", "status:not-started"],
    )

    selection = worktree_issues.build_queue([lower, higher], mode="open-task", from_issue=23)

    assert "starting from issue #23" in selection.source_note
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


def test_evidence_drift_findings_warns_for_in_progress_issue_without_local_evidence(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    issue = _issue(
        number=22,
        task_id="TASK-015",
        seq=150,
        labels=["type:task", "status:in-progress"],
    )

    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: None)

    findings = worktree_issues.evidence_drift_findings(root, [issue])

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "no local linked worktree or .build evidence" in findings[0].message


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


def test_record_issue_handoff_event_dedupes_by_idempotency_key(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    issue = _issue(number=33, task_id="TASK-033", seq=330)

    first = worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue=issue,
        branch="wt/task/33-test-issue-33",
        worktree_path=tmp_path / "worktrees" / "wt33",
        event_type="worktree-created",
        state="worktree-ready",
        details={"source": "test"},
        idempotency_key="create:33:wt33",
    )
    second = worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue=issue,
        branch="wt/task/33-test-issue-33",
        worktree_path=tmp_path / "worktrees" / "wt33",
        event_type="worktree-created",
        state="worktree-ready",
        details={"source": "test"},
        idempotency_key="create:33:wt33",
    )

    assert first == second
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["state"] == "worktree-ready"
    assert payload["last_event_type"] == "worktree-created"
    assert len(payload["events"]) == 1
    assert payload["events"][0]["idempotency_key"] == "create:33:wt33"


def test_record_issue_handoff_event_resets_completed_session_on_new_start(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)

    worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue_number=33,
        issue_title="TASK-033: Test issue 33",
        branch="wt/task/33-old",
        worktree_path=tmp_path / "worktrees" / "wt33-old",
        event_type="handback-complete",
        state="done",
        details={"source": "old"},
        idempotency_key="done:33",
    )
    state_path = root / ".build" / "worktree-state" / "issue-33.json"
    old_payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert old_payload["events"][-1]["event_type"] == "handback-complete"

    worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue_number=33,
        issue_title="TASK-033: Test issue 33",
        branch="wt/task/33-new",
        worktree_path=tmp_path / "worktrees" / "wt33-new",
        event_type="worktree-created",
        state="worktree-ready",
        details={"source": "new"},
        idempotency_key="create:33:new",
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert [event["event_type"] for event in payload["events"]] == ["worktree-created"]
    assert payload["branch"] == "wt/task/33-new"


def test_issue_evidence_summary_reports_state_and_closeout(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    wt = tmp_path / "worktrees" / "wt33"
    wt.mkdir(parents=True, exist_ok=True)
    state_dir = root / ".build" / "worktree-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    closeout_dir = root / ".build" / "worktree-closeouts"
    closeout_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "issue-33.json"
    closeout_path = closeout_dir / "issue-33-wt_task_33-test.json"
    state_path.write_text(
        json.dumps(
            {
                "issue_number": 33,
                "state": "done",
                "last_event_type": "handback-complete",
                "last_updated_at": "2026-01-01T00:00:00Z",
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    closeout_path.write_text(
        json.dumps({"stage": "complete", "cleanup_verified": True}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        worktree_issues,
        "find_linked_worktree_for_issue",
        lambda *_args: worktree_issues.WorktreeInfo(
            path=wt,
            head="abc123",
            branch="wt/task/33-test",
            is_primary=False,
        ),
    )

    summary = worktree_issues.issue_evidence_summary(root, 33)

    assert summary["linked_worktree"] == str(wt)
    assert summary["evidence_source"] == "local"
    assert summary["state_path"] == str(state_path)
    assert summary["closeout_path"] == str(closeout_path)
    assert summary["state"]["last_event_type"] == "handback-complete"
    assert summary["closeout"]["cleanup_verified"] is True


def test_issue_evidence_summary_falls_back_to_historical_when_local_evidence_missing(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: None)
    monkeypatch.setattr(
        worktree_issues,
        "historical_issue_evidence",
        lambda *_args: {
            "preferred_branch": "wt/task/33-test",
            "branch_tip": {
                "sha": "abc123",
                "timestamp": "2026-01-01T00:00:00Z",
                "subject": "feat: test",
            },
            "log_matches": [
                {"sha": "abc123", "timestamp": "2026-01-01T00:00:00Z", "subject": "feat: test"}
            ],
        },
    )

    summary = worktree_issues.issue_evidence_summary(root, 33)

    assert summary["evidence_source"] == "historical"
    assert summary["historical"]["preferred_branch"] == "wt/task/33-test"
    assert summary["state"] is None
    assert summary["validation_receipt"] is None


def test_write_validation_receipt_writes_issue_scoped_receipt(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    wt = root / "worktrees" / "wt33"
    wt.mkdir(parents=True, exist_ok=True)

    def fake_run(cmd, *, cwd=None, **_kwargs):
        joined = " ".join(cmd)
        if joined == "git rev-parse HEAD":
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123def456\n", stderr="")
        raise AssertionError(f"unexpected command: {joined}")

    original_run = worktree_issues.run
    worktree_issues.run = fake_run
    try:
        receipt_path = worktree_issues.write_validation_receipt(
            root,
            issue_id=33,
            worktree_path=wt,
            branch="wt/task/33-test",
            check_name="validate-pre-push",
        )
    finally:
        worktree_issues.run = original_run

    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["issue_number"] == 33
    assert payload["branch"] == "wt/task/33-test"
    assert payload["head_sha"] == "abc123def456"
    assert payload["check"] == "validate-pre-push"
    assert payload["result"] == "pass"


def test_cmd_worktree_resume_open_shell_tolerates_missing_agent_namespace_attrs(monkeypatch):
    root = Path("/tmp/repo")
    wt = worktree_issues.WorktreeInfo(
        path=Path("/tmp/worktrees/wt33"),
        head="abc123",
        branch="wt/infra/33-observabilitystack",
        is_primary=False,
    )
    opened: list[Path] = []

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "list_resume_candidates", lambda _root: [wt])
    monkeypatch.setattr(worktree_issues, "select_worktree_interactive", lambda items: wt)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(worktree_issues, "run_preflight", lambda **kwargs: None)
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "open_shell", lambda path: opened.append(path))
    monkeypatch.setattr(
        worktree_issues,
        "handoff_to_agent_or_shell",
        lambda **kwargs: pytest.fail("handoff_to_agent_or_shell should not be used"),
    )

    args = argparse.Namespace(
        path=None,
        no_preflight=False,
        open_shell=True,
        command=None,
    )
    rc = worktree_issues.cmd_worktree_resume(args)

    assert rc == 0
    assert opened == [wt.path]


def test_cmd_worktree_resume_shell_only_opens_shell_directly(monkeypatch):
    root = Path("/tmp/repo")
    wt = worktree_issues.WorktreeInfo(
        path=Path("/tmp/worktrees/wt33"),
        head="abc123",
        branch="wt/infra/33-observabilitystack",
        is_primary=False,
    )
    opened: list[Path] = []

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "list_resume_candidates", lambda _root: [wt])
    monkeypatch.setattr(worktree_issues, "select_worktree_interactive", lambda items: wt)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(worktree_issues, "run_preflight", lambda **kwargs: None)
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "open_shell", lambda path: opened.append(path))
    monkeypatch.setattr(
        worktree_issues,
        "handoff_to_agent_or_shell",
        lambda **kwargs: pytest.fail("handoff_to_agent_or_shell should not be used"),
    )

    args = argparse.Namespace(
        path=None,
        no_preflight=False,
        open_shell=True,
        shell_only=True,
        command=None,
    )
    rc = worktree_issues.cmd_worktree_resume(args)

    assert rc == 0
    assert opened == [wt.path]


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


def test_cmd_worktree_next_shell_only_opens_shell_directly(monkeypatch):
    root = Path("/tmp/repo")
    repo = "owner/repo"
    issue_33 = _issue(
        number=33,
        task_id="TASK-026",
        seq=260,
        labels=["type:task", "status:not-started", "ready"],
    )
    created: list[int] = []
    opened: list[Path] = []

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(
        worktree_issues,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue_33],
    )
    monkeypatch.setattr(
        worktree_issues,
        "build_queue",
        lambda _issues, **_kwargs: worktree_issues.QueueSelection(
            source_mode="open-task",
            items=[worktree_issues.QueueItem(issue=issue_33, runnable=True)],
        ),
    )
    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: None)
    monkeypatch.setattr(
        worktree_issues,
        "create_worktree_for_issue",
        lambda **kwargs: created.append(kwargs["issue"].number) or Path("/tmp/worktrees/wt33"),
    )
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "open_shell", lambda path: opened.append(path))
    monkeypatch.setattr(
        worktree_issues,
        "handoff_to_agent_or_shell",
        lambda **kwargs: pytest.fail("handoff_to_agent_or_shell should not be used"),
    )

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
        no_preflight=False,
        dry_run=False,
        open_shell=True,
        shell_only=True,
        agent=None,
        agent_mode=None,
        handoff=None,
        print_only=False,
    )
    rc = worktree_issues.cmd_worktree_next(args)

    assert rc == 0
    assert created == [33]
    assert opened == [Path("/tmp/worktrees/wt33")]


def test_cmd_worktree_next_existing_worktree_shell_only_opens_shell_directly(monkeypatch):
    root = Path("/tmp/repo")
    repo = "owner/repo"
    issue_33 = _issue(
        number=33,
        task_id="TASK-026",
        seq=260,
        labels=["type:task", "status:not-started", "ready"],
    )
    existing = worktree_issues.WorktreeInfo(
        path=Path("/tmp/worktrees/wt33"),
        head="abc123",
        branch="wt/infra/33-observabilitystack",
        is_primary=False,
    )
    opened: list[Path] = []

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(
        worktree_issues,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue_33],
    )
    monkeypatch.setattr(
        worktree_issues,
        "build_queue",
        lambda _issues, **_kwargs: worktree_issues.QueueSelection(
            source_mode="open-task",
            items=[worktree_issues.QueueItem(issue=issue_33, runnable=True)],
        ),
    )
    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: existing)
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "run_preflight", lambda **kwargs: None)
    monkeypatch.setattr(worktree_issues, "open_shell", lambda path: opened.append(path))
    monkeypatch.setattr(
        worktree_issues,
        "handoff_to_agent_or_shell",
        lambda **kwargs: pytest.fail("handoff_to_agent_or_shell should not be used"),
    )

    args = argparse.Namespace(
        repo=None,
        stream_label=None,
        mode="auto",
        choose=True,
        allow_blocked=False,
        base_dir=None,
        base_ref=None,
        scope=None,
        slug=None,
        name=None,
        no_claim=False,
        no_preflight=False,
        dry_run=False,
        open_shell=True,
        shell_only=True,
        agent=None,
        agent_mode=None,
        handoff=None,
        print_only=False,
    )

    monkeypatch.setattr(worktree_issues, "choose_issue_interactive", lambda selection: issue_33)

    rc = worktree_issues.cmd_worktree_next(args)

    assert rc == 0
    assert opened == [existing.path]


def test_cmd_worktree_next_with_random_agent_uses_random_default_agent(monkeypatch):
    root = Path("/tmp/repo")
    repo = "owner/repo"
    issue_33 = _issue(
        number=33,
        task_id="TASK-026",
        seq=260,
        labels=["type:task", "status:not-started", "ready"],
    )
    launched: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(
        worktree_issues,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue_33],
    )
    monkeypatch.setattr(
        worktree_issues,
        "build_queue",
        lambda _issues, **_kwargs: worktree_issues.QueueSelection(
            source_mode="open-task",
            items=[worktree_issues.QueueItem(issue=issue_33, runnable=True)],
        ),
    )
    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: None)
    monkeypatch.setattr(
        worktree_issues,
        "create_worktree_for_issue",
        lambda **kwargs: Path("/tmp/worktrees/wt33"),
    )
    monkeypatch.setattr(worktree_issues, "choose_default_launch_agent", lambda: "gemini")
    monkeypatch.setattr(
        worktree_issues,
        "handoff_to_agent_or_shell",
        lambda **kwargs: launched.update(kwargs),
    )

    args = argparse.Namespace(
        repo=None,
        stream_label=None,
        from_issue=None,
        mode="auto",
        choose=False,
        allow_blocked=False,
        base_dir=None,
        base_ref=None,
        scope=None,
        slug=None,
        name=None,
        no_claim=False,
        no_preflight=False,
        dry_run=False,
        open_shell=False,
        shell_only=False,
        agent="random",
        agent_mode="yolo",
        handoff="execute-now",
        print_only=False,
        tmux=None,
        zellij=True,
        no_mux=False,
    )

    rc = worktree_issues.cmd_worktree_next(args)

    assert rc == 0
    assert launched["agent"] == "gemini"
    assert launched["agent_mode"] == "yolo"
    assert launched["handoff"] == "execute-now"


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
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda _path: None)

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

    assert "Context: issue #53;" in prompt
    assert "repo owner/repo;" in prompt
    assert "branch wt/infra/53-explicit-dod;" in prompt
    assert f"worktree {wt};" in prompt
    assert "labels type:task." in prompt
    assert "Read: CLAUDE.md; docs/ARCHITECTURE.md;" in prompt
    assert "CLAUDE.md" in prompt
    assert "docs/ARCHITECTURE.md" in prompt
    assert "Scope: only this issue. Do not broaden scope." in prompt
    assert "Use: prefer GitNexus when available." in prompt
    assert "context/impact before editing shared symbols" in prompt
    assert "detect_changes before commit" in prompt
    assert "If GitNexus is unavailable, use rg and direct file reads." in prompt
    assert (
        "Loop: inspect; plan; implement; run make preflight-session; fix; repeat until done."
        in prompt
    )
    assert "make preflight-session" in prompt
    assert "Push gate: make pre-validate-session must pass before push." in prompt
    assert (
        "Done: merged PR; closed issue; cleaned worktree and branch; "
        "validation evidence recorded; make finish-worktree-close completed." in prompt
    )
    assert "Pause only if:" in prompt
    assert "report the blocker" in prompt


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

    def _list_worktrees(_root):
        return [primary, target] if target.path.exists() else [primary]

    monkeypatch.setattr(worktree_issues, "list_worktrees", _list_worktrees)
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


def test_run_gitnexus_command_clears_corrupt_npx_cache_and_retries(monkeypatch, capsys):
    calls: list[list[str]] = []

    def _subprocess_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                cmd,
                217,
                "",
                (
                    "npm error code ENOTEMPTY\n"
                    "npm error path /home/julesb/.npm/_npx/hash/node_modules/chownr\n"
                ),
            )
        return subprocess.CompletedProcess(cmd, 0, "GitNexus ready\n", "")

    removed: list[Path] = []

    monkeypatch.setattr(worktree_issues.subprocess, "run", _subprocess_run)
    monkeypatch.setattr(worktree_issues, "gitnexus_cli_path", lambda: None)
    monkeypatch.setattr(
        worktree_issues,
        "gitnexus_npx_cache_dir",
        lambda: Path("/home/julesb/.npm/_npx"),
    )
    monkeypatch.setattr(
        worktree_issues.shutil,
        "rmtree",
        lambda path, ignore_errors: removed.append(path),
    )

    proc = worktree_issues.run_gitnexus_command(Path("/tmp/repo"), ["status"], check=False)

    assert proc.returncode == 0
    assert calls == [["npx", "--yes", "gitnexus", "status"], ["npx", "--yes", "gitnexus", "status"]]
    assert removed == [Path("/home/julesb/.npm/_npx")]
    assert "clearing corrupt npx cache" in capsys.readouterr().out


def test_run_gitnexus_command_prefers_local_gitnexus_cli(monkeypatch):
    calls: list[list[str]] = []

    def _subprocess_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "GitNexus ready\n", "")

    monkeypatch.setattr(worktree_issues.subprocess, "run", _subprocess_run)
    monkeypatch.setattr(
        worktree_issues,
        "gitnexus_cli_path",
        lambda: Path("/mnt/c/Users/julia/gitnexus/gitnexus/dist/cli/index.js"),
    )
    monkeypatch.setattr(worktree_issues, "shutil_which", lambda name: f"/usr/bin/{name}")

    proc = worktree_issues.run_gitnexus_command(Path("/tmp/repo"), ["status"], check=False)

    assert proc.returncode == 0
    assert calls == [
        ["/usr/bin/node", "/mnt/c/Users/julia/gitnexus/gitnexus/dist/cli/index.js", "status"]
    ]


def test_prepare_gitnexus_for_worktree_warns_when_npm_cache_path_unavailable(monkeypatch, capsys):
    calls: list[list[str]] = []

    def _subprocess_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            217,
            "",
            (
                "npm error code ENOTEMPTY\n"
                "npm error path /home/julesb/.npm/_npx/hash/node_modules/chownr\n"
            ),
        )

    monkeypatch.setattr(worktree_issues, "gitnexus_cli_path", lambda: None)
    monkeypatch.setattr(worktree_issues, "shutil_which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(worktree_issues.subprocess, "run", _subprocess_run)
    monkeypatch.setattr(worktree_issues, "gitnexus_npx_cache_dir", lambda: None)

    worktree_issues.prepare_gitnexus_for_worktree(Path("/tmp/repo"))

    captured = capsys.readouterr()
    assert calls == [
        ["npx", "--yes", "gitnexus", "status"],
        ["npx", "--yes", "gitnexus", "analyze"],
    ]
    assert "npm cache path unavailable" in captured.err
    assert "rebuilding local index" in captured.out


def test_cmd_wt_batch_writes_manifest_and_launches_detached_agents(monkeypatch, capsys, tmp_path):
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
    created: list[int] = []
    launched: list[tuple[int, str, Path, str]] = []
    manifest_payloads: dict[Path, dict[str, object]] = {}
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(
        worktree_issues,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue_33, issue_35],
    )
    monkeypatch.setattr(
        worktree_issues,
        "build_queue",
        lambda _issues, **_kwargs: worktree_issues.QueueSelection(
            source_mode="open-task",
            items=[
                worktree_issues.QueueItem(issue=issue_33, runnable=True),
                worktree_issues.QueueItem(issue=issue_35, runnable=True),
            ],
        ),
    )
    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: None)
    monkeypatch.setattr(
        worktree_issues,
        "create_worktree_for_issue",
        lambda **kwargs: (
            created.append(kwargs["issue"].number)
            or Path(f"/tmp/worktrees/wt{kwargs['issue'].number}")
        ),
    )
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "build_agent_prompt_for_worktree", lambda *args: "prompt")
    monkeypatch.setattr(
        worktree_issues,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(worktree_issues, "batch_run_id", lambda: "run-20260320-000001")
    monkeypatch.setattr(
        worktree_issues,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "wt/task/test\n", ""),
    )

    def _write_json(path, payload):
        manifest_payloads[path] = payload
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    monkeypatch.setattr(worktree_issues, "write_json_file", _write_json)

    def _launch(**kwargs):
        launched.append(
            (
                kwargs["issue_number"],
                kwargs["agent"],
                kwargs["path"],
                kwargs["command"],
            )
        )
        issue_number = kwargs["issue_number"]
        wt_path = kwargs["path"]
        return worktree_issues.BatchLaunchResult(
            issue_number=issue_number,
            agent=kwargs["agent"],
            worktree_path=wt_path,
            branch="wt/task/test",
            command=kwargs["command"],
            state="running",
            pid=2000 + issue_number,
            local_status_path=wt_path / ".build" / "agent-run" / "status.json",
            stdout_log_path=wt_path / ".build" / "agent-run" / "stdout.log",
            stderr_log_path=wt_path / ".build" / "agent-run" / "stderr.log",
            detail="started detached agent process",
        )

    monkeypatch.setattr(worktree_issues, "launch_agent_detached", _launch)

    rc = worktree_issues.cmd_wt_batch(
        argparse.Namespace(
            repo=None,
            stream_label=None,
            mode="auto",
            count=2,
            agents="gemini",
            agent_mode="yolo",
            base_dir=None,
            interactive=False,
            dry_run=False,
        )
    )

    out = capsys.readouterr().out

    assert rc == 0
    assert created == [33, 35]
    assert [item[0] for item in launched] == [33, 35]
    manifest_path = root / ".build" / "worktree-runs" / "run-20260320-000001" / "manifest.json"
    assert manifest_path in manifest_payloads
    assert manifest_payloads[manifest_path]["run_id"] == "run-20260320-000001"
    assert manifest_payloads[manifest_path]["count_selected"] == 2
    assert len(manifest_payloads[manifest_path]["entries"]) == 2
    assert "Batch run: 2 issue(s)" in out
    assert "Run id:   run-20260320-000001" in out
    assert f"Manifest: {manifest_path}" in out
    assert "[1/2] #33 -> starting" in out
    assert "[1/2] #33 -> running pid=2033" in out
    assert "[2/2] #35 -> starting" in out
    assert "[2/2] #35 -> running pid=2035" in out
    assert "Run summary:" in out


def test_cmd_wt_batch_reuses_existing_worktree_when_agent_not_running(
    monkeypatch, capsys, tmp_path
):
    repo = "owner/repo"
    issue_41 = _issue(
        number=41,
        task_id="TASK-041",
        seq=410,
        labels=["type:task", "status:not-started", "ready"],
    )
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    existing = worktree_issues.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt41",
        head="abc123",
        branch="wt/infra/41-test",
        is_primary=False,
    )
    launched: list[Path] = []

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(
        worktree_issues,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue_41],
    )
    monkeypatch.setattr(
        worktree_issues,
        "build_queue",
        lambda _issues, **_kwargs: worktree_issues.QueueSelection(
            source_mode="open-task",
            items=[worktree_issues.QueueItem(issue=issue_41, runnable=True)],
        ),
    )
    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: existing)
    monkeypatch.setattr(worktree_issues, "worktree_agent_running", lambda path: False)
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda path: None)
    monkeypatch.setattr(worktree_issues, "build_agent_prompt_for_worktree", lambda *args: "prompt")
    monkeypatch.setattr(
        worktree_issues,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(worktree_issues, "batch_run_id", lambda: "run-20260320-000002")
    monkeypatch.setattr(
        worktree_issues,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "wt/infra/41-test\n", ""),
    )

    def _launch(**kwargs):
        launched.append(kwargs["path"])
        return worktree_issues.BatchLaunchResult(
            issue_number=41,
            agent=kwargs["agent"],
            worktree_path=kwargs["path"],
            branch="wt/infra/41-test",
            command=kwargs["command"],
            state="running",
            pid=2041,
            local_status_path=kwargs["path"] / ".build" / "agent-run" / "status.json",
            stdout_log_path=kwargs["path"] / ".build" / "agent-run" / "stdout.log",
            stderr_log_path=kwargs["path"] / ".build" / "agent-run" / "stderr.log",
            detail="started detached agent process",
        )

    monkeypatch.setattr(worktree_issues, "launch_agent_detached", _launch)
    monkeypatch.setattr(worktree_issues, "write_json_file", worktree_issues.write_json_file)
    monkeypatch.setattr(
        worktree_issues,
        "create_worktree_for_issue",
        lambda **kwargs: pytest.fail("create_worktree_for_issue should not be used"),
    )

    rc = worktree_issues.cmd_wt_batch(
        argparse.Namespace(
            repo=None,
            stream_label=None,
            mode="auto",
            count=1,
            agents="gemini",
            agent_mode="yolo",
            base_dir=None,
            interactive=False,
            dry_run=False,
        )
    )

    out = capsys.readouterr().out

    assert rc == 0
    assert launched == [existing.path]
    assert "Batch run: 1 issue(s)" in out
    assert "[1/1] #41 -> starting" in out
    assert "[1/1] #41 -> running pid=2041" in out


def test_cmd_wt_batch_skips_existing_worktree_with_running_agent(monkeypatch, capsys, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    repo = "owner/repo"
    issue_41 = _issue(
        number=41,
        task_id="TASK-041",
        seq=410,
        labels=["type:task", "status:not-started", "ready"],
    )
    issue_42 = _issue(
        number=42,
        task_id="TASK-042",
        seq=420,
        labels=["type:task", "status:not-started", "ready"],
    )
    existing = worktree_issues.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt41",
        head="abc123",
        branch="wt/infra/41-test",
        is_primary=False,
    )
    created: list[int] = []

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(
        worktree_issues,
        "fetch_repo_issues",
        lambda *_args, **_kwargs: [issue_41, issue_42],
    )
    monkeypatch.setattr(
        worktree_issues,
        "build_queue",
        lambda _issues, **_kwargs: worktree_issues.QueueSelection(
            source_mode="open-task",
            items=[
                worktree_issues.QueueItem(issue=issue_41, runnable=True),
                worktree_issues.QueueItem(issue=issue_42, runnable=True),
            ],
        ),
    )
    monkeypatch.setattr(
        worktree_issues,
        "find_linked_worktree_for_issue",
        lambda _root, issue_number: existing if issue_number == 41 else None,
    )
    monkeypatch.setattr(
        worktree_issues, "worktree_agent_running", lambda path: path == existing.path
    )
    monkeypatch.setattr(
        worktree_issues,
        "create_worktree_for_issue",
        lambda **kwargs: (
            created.append(kwargs["issue"].number)
            or tmp_path / "worktrees" / f"wt{kwargs['issue'].number}"
        ),
    )
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda path: None)
    monkeypatch.setattr(worktree_issues, "build_agent_prompt_for_worktree", lambda *args: "prompt")
    monkeypatch.setattr(
        worktree_issues,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(worktree_issues, "batch_run_id", lambda: "run-20260320-000003")
    monkeypatch.setattr(
        worktree_issues,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "wt/infra/42-test\n", ""),
    )
    monkeypatch.setattr(
        worktree_issues,
        "launch_agent_detached",
        lambda **kwargs: worktree_issues.BatchLaunchResult(
            issue_number=kwargs["issue_number"],
            agent=kwargs["agent"],
            worktree_path=kwargs["path"],
            branch="wt/infra/42-test",
            command=kwargs["command"],
            state="running",
            pid=2042,
            local_status_path=kwargs["path"] / ".build" / "agent-run" / "status.json",
            stdout_log_path=kwargs["path"] / ".build" / "agent-run" / "stdout.log",
            stderr_log_path=kwargs["path"] / ".build" / "agent-run" / "stderr.log",
            detail="started detached agent process",
        ),
    )

    rc = worktree_issues.cmd_wt_batch(
        argparse.Namespace(
            repo=None,
            stream_label=None,
            mode="auto",
            count=2,
            agents="gemini",
            agent_mode="yolo",
            base_dir=None,
            interactive=False,
            dry_run=False,
        )
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert created == [42]
    assert f"Skipping #41: agent already running in {existing.path}" in out
    assert "WARNING: only 1 runnable issue(s) available (requested 2)" in out
    assert "[1/1] #42 -> running pid=2042" in out


def test_launch_agent_detached_writes_runtime_state(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    worktree = tmp_path / "wt41"
    root.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda path: None)

    result = worktree_issues.launch_agent_detached(
        root=root,
        run_id="run-20260320-000004",
        issue_number=41,
        path=worktree,
        branch="wt/infra/41-test",
        agent="gemini",
        command='python3 -c "import time; time.sleep(5)"',
    )

    try:
        assert result.state == "running"
        assert result.pid is not None
        assert worktree_issues.pid_is_running(result.pid) is True
        assert result.local_status_path is not None and result.local_status_path.exists()
        assert result.stdout_log_path is not None and result.stdout_log_path.exists()
        assert result.stderr_log_path is not None and result.stderr_log_path.exists()
        pid_path = worktree / ".build" / "agent-run" / "pid"
        assert pid_path.read_text(encoding="utf-8").strip() == str(result.pid)
        status = json.loads(result.local_status_path.read_text(encoding="utf-8"))
        assert status["run_id"] == "run-20260320-000004"
        assert status["issue_number"] == 41
        assert status["branch"] == "wt/infra/41-test"
        assert status["agent"] == "gemini"
        assert status["state"] == "running"
        assert status["backend"] == "detached"
        assert status["pid"] == result.pid
        assert status["orchestrator_manifest"].endswith(
            ".build/worktree-runs/run-20260320-000004/manifest.json"
        )
        assert worktree_issues.worktree_agent_running(worktree) is True
    finally:
        if result.pid is not None and worktree_issues.pid_is_running(result.pid):
            os.kill(result.pid, signal.SIGTERM)
            subprocess.run(["bash", "-lc", f"wait {result.pid}"], check=False)


def test_launch_agent_detached_rejects_tty_only_agents(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    worktree = tmp_path / "wt41"
    root.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda path: None)

    with pytest.raises(worktree_issues.CliError, match="does not support detached startup"):
        worktree_issues.launch_agent_detached(
            root=root,
            run_id="run-20260320-tty-only",
            issue_number=41,
            path=worktree,
            branch="wt/infra/41-test",
            agent="codex",
            command="codex --yolo test",
        )


def test_launch_agent_detached_marks_early_exit_failed(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    worktree = tmp_path / "wt41"
    root.mkdir(parents=True, exist_ok=True)
    worktree.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda path: None)
    created: dict[str, object] = {}

    class _FakeProc:
        pid = 4242

        def wait(self, timeout=None):
            return 1

    def _popen(cmd, **kwargs):
        created["cmd"] = cmd
        created["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(worktree_issues.subprocess, "Popen", _popen)

    result = worktree_issues.launch_agent_detached(
        root=root,
        run_id="run-20260320-fail-fast",
        issue_number=41,
        path=worktree,
        branch="wt/infra/41-test",
        agent="gemini",
        command="false",
    )

    assert result.state == "failed"
    assert "startup probe" in result.detail
    assert result.local_status_path is not None
    assert created["cmd"][:2] == ["bash", "-lc"]
    status = json.loads(result.local_status_path.read_text(encoding="utf-8"))
    assert status["state"] == "failed"
    assert worktree_issues.worktree_agent_running(worktree) is False


def test_cmd_wt_batch_rejects_tty_only_agent_pool_in_detached_mode(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)

    with pytest.raises(worktree_issues.CliError, match="Detached wt-batch does not support"):
        worktree_issues.cmd_wt_batch(
            argparse.Namespace(
                repo=None,
                stream_label=None,
                mode="auto",
                count=1,
                agents="codex",
                agent_mode="yolo",
                base_dir=None,
                interactive=False,
                dry_run=False,
            )
        )


def test_cmd_wt_batch_interactive_launches_tmux_session(monkeypatch, capsys, tmp_path):
    repo = "owner/repo"
    issue_33 = _issue(
        number=33,
        task_id="TASK-026",
        seq=260,
        labels=["type:task", "status:not-started", "ready"],
    )
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    tmux_calls: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(worktree_issues, "fetch_repo_issues", lambda *_args, **_kwargs: [issue_33])
    monkeypatch.setattr(
        worktree_issues,
        "build_queue",
        lambda _issues, **_kwargs: worktree_issues.QueueSelection(
            source_mode="open-task",
            items=[worktree_issues.QueueItem(issue=issue_33, runnable=True)],
        ),
    )
    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: None)
    monkeypatch.setattr(
        worktree_issues,
        "create_worktree_for_issue",
        lambda **kwargs: tmp_path / "worktrees" / f"wt{kwargs['issue'].number}",
    )
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda _path: None)
    monkeypatch.setattr(worktree_issues, "build_agent_prompt_for_worktree", lambda *args: "prompt")
    monkeypatch.setattr(
        worktree_issues,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(worktree_issues, "batch_run_id", lambda: "run-20260320-000005")
    monkeypatch.setattr(
        worktree_issues,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "wt/task/test\n", ""),
    )
    monkeypatch.setattr(worktree_issues, "tmux_available", lambda: True)
    monkeypatch.setattr(
        worktree_issues,
        "worktree_session_pair",
        lambda label: worktree_issues.SessionPair(label=label, session_name="wt-batch-20260320"),
    )
    monkeypatch.setattr(
        worktree_issues,
        "launch_tmux_batch_session",
        lambda **kwargs: tmux_calls.update(kwargs),
    )

    rc = worktree_issues.cmd_wt_batch(
        argparse.Namespace(
            repo=None,
            stream_label=None,
            mode="auto",
            count=1,
            agents="codex",
            agent_mode="yolo",
            base_dir=None,
            interactive=True,
            dry_run=False,
        )
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert tmux_calls["session_name"] == "wt-batch-20260320"
    assert tmux_calls["attach"] is True
    assert tmux_calls["announce_windows"] is True
    assert tmux_calls["launches"] == [
        (
            "wt33",
            tmp_path / "worktrees" / "wt33",
            "codex:yolo:prompt",
        )
    ]
    assert "interactive: tmux session wt-batch-20260320" in out
    status_path = tmp_path / "worktrees" / "wt33" / ".build" / "agent-run" / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["backend"] == "tmux"
    assert status["state"] == "interactive"
    assert status["session_name"] == "wt-batch-20260320"


def test_cleanup_finished_worktree_changes_out_of_target_before_remove(
    monkeypatch, capsys, tmp_path
):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    target = worktree_issues.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt153",
        head="abc123",
        branch="wt/task/153-sample",
        is_primary=False,
    )
    target.path.mkdir(parents=True, exist_ok=True)
    changed_to: list[Path] = []
    branch_deleted = False

    monkeypatch.setattr(worktree_issues.os, "getcwd", lambda: str(target.path))
    monkeypatch.setattr(worktree_issues.os, "chdir", lambda path: changed_to.append(Path(path)))
    monkeypatch.setattr(
        worktree_issues,
        "local_branch_exists",
        lambda _root, _branch: not branch_deleted,
    )

    def _run(cmd, *, cwd=None, **_kwargs):
        nonlocal branch_deleted
        if cmd[:3] == ["git", "worktree", "remove"]:
            target.path.rmdir()
        if cmd[:3] == ["git", "branch", "-d"]:
            branch_deleted = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(worktree_issues, "run", _run)

    result = worktree_issues.cleanup_finished_worktree(root, target)
    out = capsys.readouterr().out

    assert changed_to == [root]
    assert result == {
        "worktree_removed": True,
        "branch_deleted": True,
        "worktree_pruned": True,
    }
    assert f"Removed worktree {target.path}" in out


def test_launch_tmux_batch_session_starts_grid(monkeypatch, capsys):
    launches = [
        ("wt33", Path("/tmp/worktrees/wt33"), "codex --yolo"),
        ("wt35", Path("/tmp/worktrees/wt35"), "gemini --normal"),
    ]
    calls: list[list[str]] = []
    attached: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "tmux_session_exists", lambda _name: False)

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(bin_path, args):
        attached["bin_path"] = bin_path
        attached["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_tmux_batch_session(
            session_name="worktrees",
            launches=launches,
            attach=True,
            announce_windows=False,
        )

    out = capsys.readouterr().out
    assert "tmux session 'worktrees' launching with 2 worktree window(s)" in out
    assert calls[0][:4] == ["tmux", "new-session", "-d", "-s"]
    assert calls[0][4] == "worktrees"
    assert calls[1][:3] == ["tmux", "split-window", "-h"]
    assert any(cmd[:3] == ["tmux", "new-window", "-t"] for cmd in calls)
    assert any(cmd[:3] == ["tmux", "select-window", "-t"] for cmd in calls)
    assert attached["bin_path"] == "tmux"
    assert attached["args"] == ["tmux", "attach-session", "-t", "worktrees"]


def test_launch_tmux_batch_session_replaces_existing_session(monkeypatch, capsys):
    launches = [("wt33", Path("/tmp/worktrees/wt33"), "codex --yolo")]
    calls: list[list[str]] = []
    attached: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "tmux_session_exists", lambda _name: True)

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(bin_path, args):
        attached["bin_path"] = bin_path
        attached["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_tmux_batch_session(
            session_name="worktrees",
            launches=launches,
            attach=True,
            announce_windows=True,
        )

    out = capsys.readouterr().out
    assert "already exists — replacing." in out
    assert calls[0] == ["tmux", "kill-session", "-t", "worktrees"]
    assert attached["args"] == ["tmux", "attach-session", "-t", "worktrees"]


def test_launch_zellij_session_adds_layout_to_existing_session(monkeypatch, capsys):
    path = Path("/tmp/worktrees/wt33")
    captured: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)
    monkeypatch.setattr(
        worktree_issues,
        "worktree_session_pair",
        lambda label: worktree_issues.SessionPair(
            label=label, session_name="wt33-20260319-213333-000003"
        ),
    )

    def _execvp(bin_path, args):
        captured["bin_path"] = bin_path
        captured["args"] = args

    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    worktree_issues.launch_zellij_session(
        path=path,
        agent_command="codex --yolo",
        attach=True,
    )

    out = capsys.readouterr().out
    assert "already exists — attaching." in out
    assert captured["bin_path"] == "/home/julesb/bin/zellij"
    assert captured["args"] == ["/home/julesb/bin/zellij", "attach", "wt33-20260319-213333-000003"]
    assert "Session label: wt33" in out
    assert "Session name:  wt33-20260319-213333-000003" in out


def test_launch_zellij_batch_session_adds_tabs_to_existing_session(monkeypatch, capsys):
    launches = [
        ("wt33", Path("/tmp/worktrees/wt33"), "codex --yolo"),
        ("wt35", Path("/tmp/worktrees/wt35"), "gemini --normal"),
    ]
    captured: dict[str, object] = {}
    run_calls: list[list[str]] = []
    asset_dir = Path("/tmp/batch-assets-existing")

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)

    def _mkdtemp(*, prefix):
        asset_dir.mkdir(parents=True, exist_ok=True)
        return str(asset_dir)

    def _run(cmd, **kwargs):
        run_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(bin_path, args):
        captured["bin_path"] = bin_path
        captured["args"] = args

    monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)
    monkeypatch.setattr(worktree_issues, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    worktree_issues.launch_zellij_batch_session(
        session_name="worktrees",
        launches=launches,
        attach=True,
    )

    out = capsys.readouterr().out
    assert "already exists — replacing." in out
    assert run_calls == [["/home/julesb/bin/zellij", "delete-session", "worktrees"]]
    assert captured["bin_path"] == "bash"
    assert captured["args"][0] == "bash"
    assert captured["args"][1] == "-lc"
    assert "--session worktrees" in captured["args"][2]
    layout = (asset_dir / "layout.kdl").read_text(encoding="utf-8")
    assert f'pane command="{asset_dir / "wt33-agent.sh"}"' in layout
    assert f'pane command="{asset_dir / "wt35-agent.sh"}"' in layout


def test_close_issue_done_normalizes_labels_for_already_closed_issue(monkeypatch, capsys, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    target = worktree_issues.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt153",
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
    comments: list[list[str]] = []
    cleanup_calls: list[tuple[list[str], Path | None]] = []
    branch_deleted = False

    target.path.mkdir(parents=True, exist_ok=True)

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
        if args[:2] == ["issue", "comment"]:
            comments.append(args)
        else:
            edits.append(args)
        return ""

    monkeypatch.setattr(worktree_issues, "gh_text", _gh_text)
    monkeypatch.setattr(worktree_issues, "issue_has_handback_comment", lambda **_kwargs: False)
    monkeypatch.setattr(worktree_issues, "ensure_label_exists", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worktree_issues,
        "local_branch_exists",
        lambda _root, _branch: not branch_deleted,
    )

    def _run(cmd, *, cwd=None, **_kwargs):
        nonlocal branch_deleted
        cleanup_calls.append((cmd, cwd))
        if cmd[:3] == ["git", "worktree", "remove"]:
            target.path.rmdir()
        if cmd[:3] == ["git", "branch", "-d"]:
            branch_deleted = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(worktree_issues, "run", _run)

    worktree_issues.record_issue_handoff_event(
        root=root,
        repo="owner/repo",
        issue_number=153,
        issue_title=target.branch,
        branch=target.branch,
        worktree_path=target.path,
        event_type="worktree-resumed",
        state="worktree-ready",
        details={"source": "test"},
        idempotency_key="resume:153:test",
    )

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
    assert len(comments) == 1
    assert comments[0][:5] == ["issue", "comment", "153", "-R", "owner/repo"]
    assert "Execution evidence: PASS" in comments[0][6]
    assert "Evidence hash:" in comments[0][6]
    assert cleanup_calls == [
        (["git", "worktree", "remove", str(target.path)], root),
        (["git", "branch", "-d", "wt/task/153-sample"], root),
        (["git", "worktree", "prune"], root),
    ]
    assert "Issue #153 already closed." in out
    assert "Normalized closed-issue lifecycle labels." in out
    assert "Cleaning up worktree..." in out
    assert f"Removed worktree {target.path}" in out
    assert "Deleted branch wt/task/153-sample" in out
    assert "Pruned stale worktree refs" in out
    report_path = root / ".build" / "worktree-closeouts" / "issue-153-wt_task_153-sample.json"
    assert f"Closeout report: {report_path}" in out
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["stage"] == "complete"
    assert report["issue_closed"] is True
    assert report["cleanup_verified"] is True
    assert report["cleanup"] == {
        "branch_deleted": True,
        "worktree_pruned": True,
        "worktree_removed": True,
    }
    assert [event["stage"] for event in report["events"]] == [
        "starting",
        "merge-check",
        "issue-close",
        "cleanup",
        "cleanup-verified",
    ]
    assert report["events"][0]["message"] == "closeout started"
    assert report["events"][-1]["message"] == "cleanup verified"
    assert all(isinstance(event["ts"], str) for event in report["events"])
    assert all(isinstance(event["pid"], int) for event in report["events"])
    state_path = root / ".build" / "worktree-state" / "issue-153.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["state"] == "done"
    assert state["last_event_type"] == "handback-complete"
    assert [event["event_type"] for event in state["events"]] == [
        "worktree-resumed",
        "closeout-started",
        "closeout-complete",
        "handback-audited",
        "handback-complete",
    ]


def test_cmd_agent_handoff_defaults_to_codex_yolo_execute_now(monkeypatch):
    root = Path("/tmp/repo")
    wt = Path("/tmp/worktrees/wt314")
    recorded: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: "owner/repo")
    monkeypatch.setattr(worktree_issues, "current_path", lambda: wt)
    monkeypatch.setattr(
        worktree_issues,
        "current_branch",
        lambda _path: "wt/task/314-reserved-platform-tenant-and-control-plane-agent-model",
    )
    monkeypatch.setattr(
        worktree_issues,
        "handoff_to_agent_or_shell",
        lambda **kwargs: recorded.update(kwargs),
    )

    rc = worktree_issues.cmd_agent_handoff(
        argparse.Namespace(
            repo=None,
            path=None,
            agent=None,
            agent_mode=None,
            handoff=None,
            print_only=False,
            tmux=None,
            zellij=None,
            no_mux=False,
        )
    )

    assert rc == 0
    assert recorded["path"] == wt
    assert recorded["agent"] == "codex"
    assert recorded["agent_mode"] == "yolo"
    assert recorded["handoff"] == "execute-now"


def test_append_issue_handback_comment_skips_existing_hash(monkeypatch):
    posted: list[list[str]] = []

    monkeypatch.setattr(
        worktree_issues,
        "gh_json",
        lambda *_args, **_kwargs: {
            "comments": [{"body": "Execution evidence: PASS\nEvidence hash: abc123"}]
        },
    )
    monkeypatch.setattr(
        worktree_issues,
        "gh_text",
        lambda args, **kwargs: posted.append(args) or "",
    )

    worktree_issues.append_issue_handback_comment(
        root=Path("/tmp/repo"),
        repo="owner/repo",
        issue_id=153,
        summary={"evidence_hash": "abc123"},
    )

    assert posted == []


def test_cmd_finish_close_json_prints_closeout_report(monkeypatch, capsys, tmp_path):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    target = worktree_issues.WorktreeInfo(
        path=tmp_path / "worktrees" / "wt153",
        head="abc123",
        branch="wt/task/153-sample",
        is_primary=False,
    )
    report_path = root / ".build" / "worktree-closeouts" / "issue-153-wt_task_153-sample.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_payload = {
        "branch": target.branch,
        "events": [
            {
                "stage": "complete",
                "message": "done",
                "pid": 1,
                "ts": "2026-01-01T00:00:00Z",
            }
        ],
        "issue_closed": True,
        "issue_id": 153,
        "merged_pr_required": True,
        "repo": "owner/repo",
        "stage": "complete",
        "worktree_path": str(target.path),
    }

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "list_worktrees", lambda _root: [target])
    monkeypatch.setattr(worktree_issues, "resolve_current_worktree", lambda _path, _wts: target)
    monkeypatch.setattr(worktree_issues, "current_path", lambda: target.path)
    monkeypatch.setattr(worktree_issues, "closeout_report_path", lambda _root, _target: report_path)
    monkeypatch.setattr(
        worktree_issues,
        "close_issue_done",
        lambda *_args, **_kwargs: report_path.write_text(
            json.dumps(report_payload, indent=2) + "\n", encoding="utf-8"
        ),
    )

    rc = worktree_issues.cmd_finish_close(argparse.Namespace(path=None, force=False, json=True))
    out = capsys.readouterr().out

    assert rc == 0
    assert json.loads(out.splitlines()[-1]) == report_payload


def test_launch_zellij_session_starts_or_adds_with_layout(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    asset_dir = tmp_path / "session-assets"

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: False)

    def _mkdtemp(*, prefix):
        asset_dir.mkdir(parents=True, exist_ok=True)
        return str(asset_dir)

    monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)

    def _execvp(file, args):
        calls.append([file, *args[1:]])
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_zellij_session(
            path=tmp_path,
            agent_command="echo agent",
            session_name="wt123",
            attach=True,
        )

    assert calls
    assert calls[0][0] == "bash"
    assert calls[0][1] == "-lc"
    assert "rm -rf " in calls[0][2]
    assert "--new-session-with-layout" in calls[0][2]
    assert "--session wt123" in calls[0][2]
    layout = (asset_dir / "layout.kdl").read_text(encoding="utf-8")
    assert 'args "-lc"' not in layout
    assert f'pane command="{asset_dir / "agent.sh"}"' in layout
    assert f'pane command="{asset_dir / "shell.sh"}"' in layout
    assert (asset_dir / "agent.sh").read_text(encoding="utf-8").endswith("\n")
    assert (asset_dir / "shell.sh").read_text(encoding="utf-8").endswith("\n")


def test_launch_zellij_session_adds_tab_to_existing_session(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    subprocess_calls: list[list[str]] = []

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)
    monkeypatch.setattr(
        worktree_issues,
        "worktree_session_pair",
        lambda label: worktree_issues.SessionPair(
            label=label, session_name="wt123-20260319-213333-000004"
        ),
    )

    def _run(cmd, **kwargs):
        subprocess_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(file, args):
        calls.append([file, *args[1:]])
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_zellij_session(
            path=tmp_path,
            agent_command="echo agent",
            session_name="wt123",
            attach=True,
        )

    assert calls
    assert subprocess_calls == [["stty", "-ixon"]]
    assert calls[0][0] == "/home/julesb/bin/zellij"
    assert calls[0][1:] == ["attach", "wt123"]


def test_zellij_session_exists_handles_ansi_colored_output(monkeypatch):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            "\x1b[32;1mwt278\x1b[m [Created \x1b[35;1m0s\x1b[m ago]\n",
            "",
        )

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)

    assert worktree_issues.zellij_session_exists("wt278") is True


def test_launch_zellij_batch_session_starts_or_adds_with_layout(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    subprocess_calls: list[list[str]] = []
    asset_dir = tmp_path / "batch-assets"

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: False)

    def _mkdtemp(*, prefix):
        asset_dir.mkdir(parents=True, exist_ok=True)
        return str(asset_dir)

    monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)

    def _run(cmd, **kwargs):
        subprocess_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(file, args):
        calls.append([file, *args[1:]])
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_zellij_batch_session(
            session_name="worktrees",
            launches=[("wt123", tmp_path, "echo agent")],
            attach=True,
        )

    assert calls
    assert subprocess_calls == [["stty", "-ixon"]]
    assert calls[0][0] == "bash"
    assert calls[0][1] == "-lc"
    assert "rm -rf " in calls[0][2]
    assert "--new-session-with-layout" in calls[0][2]
    assert "--session worktrees" in calls[0][2]
    layout = (asset_dir / "layout.kdl").read_text(encoding="utf-8")
    assert 'args "-lc"' not in layout
    assert f'pane command="{asset_dir / "wt123-agent.sh"}"' in layout
    assert f'pane command="{asset_dir / "wt123-shell.sh"}"' in layout


def test_launch_zellij_batch_session_adds_to_existing_session(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    subprocess_calls: list[list[str]] = []
    asset_dir = tmp_path / "batch-assets-existing"

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)

    def _mkdtemp(*, prefix):
        asset_dir.mkdir(parents=True, exist_ok=True)
        return str(asset_dir)

    def _run(cmd, **kwargs):
        subprocess_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(file, args):
        calls.append([file, *args[1:]])
        raise SystemExit(0)

    monkeypatch.setattr(tempfile, "mkdtemp", _mkdtemp)
    monkeypatch.setattr(worktree_issues, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_zellij_batch_session(
            session_name="worktrees",
            launches=[("wt123", tmp_path, "echo agent")],
            attach=True,
        )

    assert calls
    assert subprocess_calls == [["/home/julesb/bin/zellij", "delete-session", "worktrees"]]
    assert calls[0][0] == "bash"
    assert calls[0][1] == "-lc"
    assert "--session worktrees" in calls[0][2]
    layout = (asset_dir / "layout.kdl").read_text(encoding="utf-8")
    assert f'pane command="{asset_dir / "wt123-agent.sh"}"' in layout
