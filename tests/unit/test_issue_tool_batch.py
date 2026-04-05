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
