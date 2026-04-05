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


def test_launch_tmux_session_uses_reported_initial_window_index(monkeypatch, capsys):
    path = Path("/tmp/worktrees/wt318")
    calls: list[list[str]] = []
    attached: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "tmux_session_exists", lambda _name: False)

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:3] == ["tmux", "list-panes", "-t"]:
            return subprocess.CompletedProcess(cmd, 0, "wt318:1.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _execvp(bin_path, args):
        attached["bin_path"] = bin_path
        attached["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.subprocess, "run", _run)
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_tmux_session(
            path=path,
            agent_command="claude --dangerously-skip-permissions prompt",
            attach=True,
        )

    out = capsys.readouterr().out
    assert "tmux session 'wt318' launching in /tmp/worktrees/wt318" in out
    assert calls[0][:4] == ["tmux", "new-session", "-d", "-s"]
    assert [
        "tmux",
        "list-panes",
        "-t",
        "wt318",
        "-F",
        "#{session_name}:#{window_index}.#{pane_index}",
    ] in calls
    assert ["tmux", "rename-window", "-t", "wt318:1", "wt318"] in calls
    assert ["tmux", "split-window", "-h", "-t", "wt318:1", "-c", "/tmp/worktrees/wt318"] in calls
    assert any(cmd[:4] == ["tmux", "send-keys", "-t", "wt318:1.1"] for cmd in calls)
    assert any(cmd[:4] == ["tmux", "send-keys", "-t", "wt318:1.0"] for cmd in calls)
    assert attached["args"] == ["tmux", "attach-session", "-t", "wt318"]


def test_handoff_to_agent_or_shell_falls_back_when_tmux_launch_fails(monkeypatch, tmp_path, capsys):
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    wt = tmp_path / "worktrees" / "wt381"
    wt.mkdir(parents=True, exist_ok=True)
    execvp_call: dict[str, object] = {}

    def _run_prompt(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "wt/task/381-something\n", "")

    def _execvp(bin_path, args):
        execvp_call["bin_path"] = bin_path
        execvp_call["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues, "run", _run_prompt)
    monkeypatch.setattr(worktree_issues, "worktree_issue_id", lambda _path: 381)
    monkeypatch.setattr(
        worktree_issues,
        "fetch_issue_labels_for_prompt",
        lambda _root, _repo, _issue: "enhancement|type:task|status:in-progress",
    )
    monkeypatch.setattr(worktree_issues, "ensure_uv_venv", lambda _path: None)
    monkeypatch.setattr(
        worktree_issues,
        "launch_tmux_session",
        lambda **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, ["tmux", "list-panes"])
        ),
    )
    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.handoff_to_agent_or_shell(
            path=wt,
            root=root,
            repo="owner/repo",
            agent="codex",
            agent_mode="yolo",
            handoff="execute-now",
            mux="tmux",
        )

    captured = capsys.readouterr()
    assert "WARNING: tmux launch failed" in captured.err
    assert execvp_call["bin_path"] == "bash"
    assert execvp_call["args"][0:2] == ["bash", "-lc"]


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
    from scripts.issue_tool import github_client

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
    monkeypatch.setattr(github_client, "gh_text", _gh_text)
    monkeypatch.setattr(worktree_issues, "issue_has_handback_comment", lambda **_kwargs: False)
    monkeypatch.setattr(worktree_issues, "ensure_label_exists", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(github_client, "ensure_label_exists", lambda *_args, **_kwargs: None)
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
