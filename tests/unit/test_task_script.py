from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_task_module():
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("task_script", repo_root / "scripts" / "task.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


task_script = _load_task_module()


def _sample_task():
    return task_script.Task(
        number="011",
        status="[ ]",
        title="Test task",
        description="Do the thing.",
        phase="Phase 1",
    )


def test_wsl_version_detection():
    assert task_script._looks_like_wsl("Linux version ... microsoft-standard-WSL2")
    assert not task_script._looks_like_wsl("Linux version ... generic")


def test_detect_runtime_env_prefers_env_override(monkeypatch):
    monkeypatch.setenv("WSL", "local")
    monkeypatch.setattr(task_script, "is_wsl_environment", lambda: False)
    assert task_script.detect_runtime_env("auto") == "local-wsl"


def test_detect_runtime_env_auto_remote_when_not_wsl(monkeypatch):
    monkeypatch.delenv("WSL", raising=False)
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(task_script, "is_wsl_environment", lambda: False)
    assert task_script.detect_runtime_env("auto") == "remote"


def test_generate_prompt_marks_local_wsl_context():
    prompt = task_script.generate_prompt(
        _sample_task(),
        Path("/tmp/worktrees/TASK-011-test-task"),
        "task/011-test-task",
        "local-wsl",
    )
    assert "Context:  local-wsl" in prompt
    assert "Run `make validate-local` in this worktree" in prompt
    assert "Work only in this worktree" in prompt


def test_generate_prompt_marks_remote_context():
    prompt = task_script.generate_prompt(
        _sample_task(),
        Path("/tmp/worktrees/TASK-011-test-task"),
        "task/011-test-task",
        "remote",
    )
    assert "Context:  remote" in prompt
    assert "remote/mobile session" in prompt
    assert "Do not assume a git worktree exists" in prompt
