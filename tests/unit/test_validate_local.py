from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_validate_local_module():
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "validate_local", repo_root / "scripts" / "validate_local.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate_local = _load_validate_local_module()


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_build_task_set_fast_and_full() -> None:
    fast = validate_local.build_task_set("fast")
    full = validate_local.build_task_set("full")

    assert [task.target for task in fast] == [
        "rules-sync-audit",
        "validate-python",
        "validate-cdk-ts-local",
        "validate-secrets-diff",
    ]
    assert [task.target for task in full] == [
        "rules-sync-audit",
        "validate-python",
        "validate-cdk",
        "validate-secrets-full",
    ]


def test_run_validation_mode_fails_when_one_subtask_fails(tmp_path: Path, capsys) -> None:
    seen: list[str] = []

    def _runner(cmd, *, cwd, text, capture_output, check):
        _ = cwd, text, capture_output, check
        target = cmd[-1]
        seen.append(target)
        if target == "validate-cdk-ts-local":
            return _Completed(2, stdout="cdk failed")
        return _Completed(0, stdout=f"{target} ok")

    exit_code = validate_local.run_validation_mode(mode="fast", repo_root=tmp_path, runner=_runner)

    assert exit_code == 1
    assert seen == [
        "rules-sync-audit",
        "validate-python",
        "validate-cdk-ts-local",
        "validate-secrets-diff",
    ]
    output = capsys.readouterr().out
    assert "[FAIL] CDK TypeScript (validate-cdk-ts-local)" in output
    assert "cdk failed" in output


def test_run_validation_mode_prints_summary_for_success(tmp_path: Path, capsys) -> None:
    def _runner(cmd, *, cwd, text, capture_output, check):
        _ = cwd, text, capture_output, check
        return _Completed(0, stdout=f"{cmd[-1]} ok")

    exit_code = validate_local.run_validation_mode(mode="full", repo_root=tmp_path, runner=_runner)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "==> Validation summary" in output
    assert "[PASS] Rules sync (rules-sync-audit)" in output
    assert "[PASS] Secrets full (validate-secrets-full)" in output
