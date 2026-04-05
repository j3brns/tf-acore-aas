from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_support = importlib.import_module("issue_tool_test_support")
worktree_issues = _support.worktree_issues
_issue = _support._issue
argparse = _support.argparse
json = _support.json
os = _support.os
signal = _support.signal
subprocess = _support.subprocess
tempfile = _support.tempfile
pytest = _support.pytest


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
    assert payload["head_sha"] == "abc123def456"  # pragma: allowlist secret
    assert payload["check"] == "validate-pre-push"
    assert payload["result"] == "pass"
