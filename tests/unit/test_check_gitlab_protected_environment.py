"""Unit tests for scripts/check_gitlab_protected_environment.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "check_gitlab_protected_environment_script",
        repo_root / "scripts" / "check_gitlab_protected_environment.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


check_script = _load_module()


def test_build_api_url_escapes_project_and_environment_names() -> None:
    url = check_script.build_api_url(
        "https://gitlab.example.com/api/v4/",
        "group/platform/project",
        "prod/blue",
    )
    assert (
        url == "https://gitlab.example.com/api/v4/projects/group%2Fplatform%2Fproject/"
        "protected_environments/prod%2Fblue"
    )


def test_validate_protected_environment_accepts_top_level_required_approval_count() -> None:
    result = check_script.validate_protected_environment(
        {
            "name": "prod",
            "required_approval_count": 2,
            "approval_rules": [{"required_approvals": 2}],
            "deploy_access_levels": [{"group_id": 1}],
        },
        expected_environment_name="prod",
        min_approvals=2,
    )
    assert result.environment_name == "prod"
    assert result.required_approval_count == 2
    assert result.approval_rule_count == 1
    assert result.deploy_access_level_count == 1


def test_validate_protected_environment_falls_back_to_approval_rule_sum() -> None:
    result = check_script.validate_protected_environment(
        {
            "name": "prod",
            "approval_rules": [
                {"required_approvals": 1},
                {"required_approvals": "1"},
            ],
            "deploy_access_levels": [],
        },
        expected_environment_name="prod",
        min_approvals=2,
    )
    assert result.required_approval_count == 2


def test_validate_protected_environment_rejects_insufficient_approvals() -> None:
    with pytest.raises(
        check_script.ProtectionCheckError,
        match="requires 1 approvals; expected at least 2",
    ):
        check_script.validate_protected_environment(
            {
                "name": "prod",
                "required_approval_count": 1,
            },
            expected_environment_name="prod",
            min_approvals=2,
        )


def test_validate_protected_environment_rejects_missing_approval_metadata() -> None:
    with pytest.raises(
        check_script.ProtectionCheckError,
        match="missing approval metadata",
    ):
        check_script.validate_protected_environment(
            {"name": "prod"},
            expected_environment_name="prod",
            min_approvals=2,
        )


def test_run_passes_when_fetcher_returns_real_protected_environment() -> None:
    calls: list[tuple[str, str, str, str, float]] = []

    def _fetcher(
        api_url: str,
        project_id: str,
        environment_name: str,
        api_token: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        calls.append((api_url, project_id, environment_name, api_token, timeout_seconds))
        return {
            "name": "prod",
            "required_approval_count": 2,
            "approval_rules": [{"required_approvals": 2}],
            "deploy_access_levels": [{"group_id": 1}],
        }

    result = check_script.run(
        environment_name="prod",
        min_approvals=2,
        timeout_seconds=7.5,
        env={
            "CI_API_V4_URL": "https://gitlab.example.com/api/v4",
            "CI_PROJECT_ID": "1234",
            "GITLAB_PROTECTED_ENV_API_TOKEN": "secret-token",
        },
        fetcher=_fetcher,
    )

    assert result.required_approval_count == 2
    assert calls == [
        (
            "https://gitlab.example.com/api/v4",
            "1234",
            "prod",
            "secret-token",
            7.5,
        )
    ]


def test_run_fails_closed_when_protected_environment_is_missing() -> None:
    def _fetcher(
        api_url: str,
        project_id: str,
        environment_name: str,
        api_token: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        raise check_script.ProtectionCheckError(
            f"Protected environment {environment_name!r} was not found"
        )

    with pytest.raises(check_script.ProtectionCheckError, match="was not found"):
        check_script.run(
            environment_name="prod",
            min_approvals=2,
            env={
                "CI_API_V4_URL": "https://gitlab.example.com/api/v4",
                "CI_PROJECT_ID": "1234",
                "GITLAB_PROTECTED_ENV_API_TOKEN": "secret-token",
            },
            fetcher=_fetcher,
        )


def test_main_returns_1_when_api_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_API_V4_URL", "https://gitlab.example.com/api/v4")
    monkeypatch.setenv("CI_PROJECT_ID", "1234")
    monkeypatch.delenv("GITLAB_PROTECTED_ENV_API_TOKEN", raising=False)

    assert check_script.main(["--environment", "prod", "--min-approvals", "2"]) == 1
