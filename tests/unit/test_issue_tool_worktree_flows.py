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
