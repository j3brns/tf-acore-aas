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
    assert "Loop: inspect; plan; implement; run make preflight-session; fix; repeat;" in prompt
    assert "Do not stop at PR creation" in prompt
    assert "make preflight-session" in prompt
    assert "Push gate: make pre-validate-session must pass before push." in prompt
    assert (
        "Done: only when the PR is merged to the target branch; the issue is closed "
        "and normalized; validation evidence is recorded; .build hand-back evidence "
        "is finalized; and make finish-worktree-close has completed successfully." in prompt
    )
    assert "do not treat worktree or branch deletion as part of semantic completion" in prompt
    assert "Pause only if:" in prompt
    assert "Otherwise estimate reasonably, keep moving" in prompt
    assert "report a blocker with the exact next command" in prompt


def test_auto_detect_mux_prefers_tmux_over_zellij(monkeypatch):
    monkeypatch.setattr(worktree_issues, "tmux_available", lambda: True)
    monkeypatch.setattr(worktree_issues, "zellij_available", lambda: True)

    assert worktree_issues.auto_detect_mux() == "tmux"


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
