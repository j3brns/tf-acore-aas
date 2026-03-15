from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_plan_dev_module():
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "plan_dev_script", repo_root / "scripts" / "plan_dev.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plan_dev = _load_plan_dev_module()


def test_parse_args_accepts_makefile_contract():
    args = plan_dev.parse_args(["Implement the billing metering pipeline"])
    assert args.task_description == "Implement the billing metering pipeline"


def test_parse_args_requires_task_description():
    with pytest.raises(SystemExit):
        plan_dev.parse_args([])


def test_build_plan_returns_structured_output_for_plan_dev_issue():
    root = Path(__file__).resolve().parents[2]
    plan = plan_dev.build_plan("Issue 235: implement make plan-dev instead of a stub script", root)

    assert "# Development Plan" in plan
    assert "Task: Issue 235: implement make plan-dev instead of a stub script" in plan
    assert "Read-first context:" in plan
    assert "Likely touched paths:" in plan
    assert "`scripts/plan_dev.py`" in plan
    assert "`Makefile`" in plan
    assert "Execution plan:" in plan
    assert "Validation checklist:" in plan


def test_makefile_plan_dev_target_matches_cli_contract():
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")

    assert 'make plan-dev TASK="Implement the billing metering pipeline"' in makefile
    assert 'uv run python scripts/plan_dev.py "$(TASK)"' in makefile
