from __future__ import annotations

import argparse
import importlib.util
import json
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
    monkeypatch.setattr(worktree_issues, "prepare_gitnexus_for_worktree", lambda _path: None)

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
    assert called["mux"] is None


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

    assert "issue #53" in prompt
    assert "wt/infra/53-explicit-dod" in prompt
    assert "docs/ARCHITECTURE.md" in prompt
    assert "make preflight-session" in prompt
    assert "make pre-validate-session" in prompt
    assert "validation evidence" in prompt
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
    assert calls == [["npx", "gitnexus", "status"], ["npx", "gitnexus", "status"]]
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
    assert calls == [["npx", "gitnexus", "status"], ["npx", "gitnexus", "analyze"]]
    assert "npm cache path unavailable" in captured.err
    assert "rebuilding local index" in captured.out


def test_cmd_wt_batch_uses_single_zellij_session_for_multiple_worktrees(monkeypatch, capsys):
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
    created: list[int] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(worktree_issues, "zellij_available", lambda: True)
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

    def _launch(*, session_name, launches, attach, announce_tabs=True):
        captured["session_name"] = session_name
        captured["launches"] = launches
        captured["attach"] = attach
        captured["announce_tabs"] = announce_tabs

    monkeypatch.setattr(worktree_issues, "launch_zellij_batch_session", _launch)

    rc = worktree_issues.cmd_wt_batch(
        argparse.Namespace(
            repo=None,
            stream_label=None,
            mode="auto",
            count=2,
            agents="gemini,codex",
            agent_mode="yolo",
            base_dir=None,
            dry_run=False,
        )
    )

    out = capsys.readouterr().out

    assert rc == 0
    assert created == [33, 35]
    assert captured["session_name"] == "worktrees"
    assert captured["attach"] is True
    assert captured["announce_tabs"] is False
    assert [tab for tab, _, _ in captured["launches"]] == ["wt33", "wt35"]
    assert "Batch session: 2 issue(s)" in out
    assert "[1/2] #33 -> starting" in out
    assert "[1/2] #33 -> ready" in out
    assert "[2/2] #35 -> starting" in out
    assert "[2/2] #35 -> ready" in out
    assert "Attach:  zellij attach worktrees" in out


def test_cmd_wt_batch_hello_world_e2e_two_issues(monkeypatch, capsys):
    root = Path("/tmp/repo")
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
    created: list[int] = []
    prepared: list[Path] = []
    launched: dict[str, object] = {}
    wt_paths: dict[int, Path] = {}

    monkeypatch.setattr(worktree_issues, "repo_root", lambda: root)
    monkeypatch.setattr(worktree_issues, "origin_repo_slug", lambda _root: repo)
    monkeypatch.setattr(worktree_issues, "zellij_available", lambda: True)
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
    monkeypatch.setattr(worktree_issues, "find_linked_worktree_for_issue", lambda *_args: None)
    monkeypatch.setattr(
        worktree_issues,
        "create_worktree_for_issue",
        lambda **kwargs: (
            created.append(kwargs["issue"].number)
            or wt_paths.setdefault(
                kwargs["issue"].number, Path(f"/tmp/worktrees/wt{kwargs['issue'].number}")
            )
        ),
    )
    monkeypatch.setattr(
        worktree_issues,
        "prepare_gitnexus_for_worktree",
        lambda path: prepared.append(path),
    )
    monkeypatch.setattr(worktree_issues, "build_agent_prompt_for_worktree", lambda *args: "prompt")
    monkeypatch.setattr(
        worktree_issues,
        "build_agent_command",
        lambda agent, mode, prompt: f"{agent}:{mode}:{prompt}",
    )
    monkeypatch.setattr(
        worktree_issues,
        "launch_zellij_batch_session",
        lambda **kwargs: launched.update(kwargs),
    )

    rc = worktree_issues.cmd_wt_batch(
        argparse.Namespace(
            repo=None,
            stream_label=None,
            mode="auto",
            count=2,
            agents="gemini,codex",
            agent_mode="yolo",
            base_dir=None,
            dry_run=False,
        )
    )

    out = capsys.readouterr().out

    assert rc == 0
    assert created == [41, 42]
    assert prepared == [wt_paths[41], wt_paths[42]]
    assert launched["session_name"] == "worktrees"
    assert launched["attach"] is True
    assert launched["announce_tabs"] is False
    assert [tab for tab, _, _ in launched["launches"]] == ["wt41", "wt42"]
    assert "Batch session: 2 issue(s)" in out
    assert "[1/2] #41 -> starting" in out
    assert "[1/2] #41 -> ready" in out
    assert "[2/2] #42 -> starting" in out
    assert "[2/2] #42 -> ready" in out


def test_launch_zellij_session_adds_layout_to_existing_session(monkeypatch, capsys):
    path = Path("/tmp/worktrees/wt33")
    captured: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)

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
    assert captured["args"] == ["/home/julesb/bin/zellij", "attach", "wt33"]


def test_launch_zellij_batch_session_adds_tabs_to_existing_session(monkeypatch, capsys):
    launches = [
        ("wt33", Path("/tmp/worktrees/wt33"), "codex --yolo"),
        ("wt35", Path("/tmp/worktrees/wt35"), "gemini --normal"),
    ]
    captured: dict[str, object] = {}

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)

    def _execvp(bin_path, args):
        captured["bin_path"] = bin_path
        captured["args"] = args

    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    worktree_issues.launch_zellij_batch_session(
        session_name="worktrees",
        launches=launches,
        attach=True,
    )

    out = capsys.readouterr().out
    assert "already exists — attaching." in out
    assert captured["bin_path"] == "/home/julesb/bin/zellij"
    assert captured["args"] == ["/home/julesb/bin/zellij", "attach", "worktrees"]


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
        edits.append(args)
        return ""

    monkeypatch.setattr(worktree_issues, "gh_text", _gh_text)
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

    monkeypatch.setattr(worktree_issues, "zellij_bin", lambda: "/home/julesb/bin/zellij")
    monkeypatch.setattr(worktree_issues, "zellij_session_exists", lambda _name: True)

    def _execvp(file, args):
        calls.append([file, *args[1:]])
        raise SystemExit(0)

    monkeypatch.setattr(worktree_issues.os, "execvp", _execvp)

    with pytest.raises(SystemExit):
        worktree_issues.launch_zellij_batch_session(
            session_name="worktrees",
            launches=[("wt123", tmp_path, "echo agent")],
            attach=True,
        )

    assert calls
    assert calls[0][0] == "/home/julesb/bin/zellij"
    assert calls[0][1:] == ["attach", "worktrees"]
