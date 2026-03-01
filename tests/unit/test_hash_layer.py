"""Unit tests for scripts/hash_layer.py (TASK-033)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws


def _load_hash_layer_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "hash_layer_script", repo_root / "scripts" / "hash_layer.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


hl: Any = _load_hash_layer_module()

_REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# compute_dependency_hash — pure function tests
# ---------------------------------------------------------------------------


def test_same_deps_same_order_gives_same_hash() -> None:
    deps = ["boto3>=1.37.0", "requests>=2.31.0", "pydantic>=2.0.0"]
    assert hl.compute_dependency_hash(deps) == hl.compute_dependency_hash(deps)


def test_order_invariant() -> None:
    """Different orderings of the same deps must produce the same hash."""
    deps_a = ["boto3>=1.37.0", "requests>=2.31.0", "pydantic>=2.0.0"]
    deps_b = ["requests>=2.31.0", "pydantic>=2.0.0", "boto3>=1.37.0"]
    deps_c = ["pydantic>=2.0.0", "boto3>=1.37.0", "requests>=2.31.0"]

    hash_a = hl.compute_dependency_hash(deps_a)
    assert hl.compute_dependency_hash(deps_b) == hash_a
    assert hl.compute_dependency_hash(deps_c) == hash_a


def test_whitespace_invariant() -> None:
    """Leading/trailing whitespace on each dep must not affect the hash."""
    deps_clean = ["boto3>=1.37.0", "requests>=2.31.0"]
    deps_padded = ["  boto3>=1.37.0  ", "\trequests>=2.31.0\n"]
    assert hl.compute_dependency_hash(deps_clean) == hl.compute_dependency_hash(deps_padded)


def test_different_deps_different_hash() -> None:
    deps_a = ["boto3>=1.37.0"]
    deps_b = ["boto3>=1.38.0"]
    assert hl.compute_dependency_hash(deps_a) != hl.compute_dependency_hash(deps_b)


def test_hash_length_is_16_chars() -> None:
    deps = ["boto3>=1.37.0"]
    result = hl.compute_dependency_hash(deps)
    assert len(result) == 16
    assert all(c in "0123456789abcdef" for c in result)


def test_empty_deps_returns_hash() -> None:
    result = hl.compute_dependency_hash([])
    assert len(result) == 16


# ---------------------------------------------------------------------------
# read_agent_deps — file-system tests against echo-agent fixture
# ---------------------------------------------------------------------------


def test_read_agent_deps_echo_agent() -> None:
    """Read real echo-agent pyproject.toml and get a non-empty dep list."""
    deps = hl.read_agent_deps("echo-agent")
    assert isinstance(deps, list)
    assert len(deps) > 0
    assert all(isinstance(d, str) for d in deps)
    # echo-agent is known to depend on bedrock-agentcore
    assert any("bedrock-agentcore" in d for d in deps)


def test_read_agent_deps_missing_agent() -> None:
    with pytest.raises(FileNotFoundError, match="pyproject.toml not found"):
        hl.read_agent_deps("nonexistent-agent-xyz")


# ---------------------------------------------------------------------------
# get_ssm_hash — SSM interaction tests (moto)
# ---------------------------------------------------------------------------


@mock_aws
def test_get_ssm_hash_returns_none_when_parameter_absent() -> None:
    result = hl.get_ssm_hash("echo-agent", _REGION)
    assert result is None


@mock_aws
def test_get_ssm_hash_returns_stored_value() -> None:
    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(
        Name="/platform/layers/echo-agent/hash",
        Value="abcd1234efgh5678",
        Type="String",
    )
    result = hl.get_ssm_hash("echo-agent", _REGION)
    assert result == "abcd1234efgh5678"


# ---------------------------------------------------------------------------
# run — integration of hash + SSM check
# ---------------------------------------------------------------------------


@mock_aws
def test_run_returns_1_when_no_ssm_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", _REGION)
    exit_code = hl.run("echo-agent")
    assert exit_code == 1


@mock_aws
def test_run_returns_0_when_hash_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", _REGION)

    deps = hl.read_agent_deps("echo-agent")
    computed = hl.compute_dependency_hash(deps)

    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(
        Name="/platform/layers/echo-agent/hash",
        Value=computed,
        Type="String",
    )

    exit_code = hl.run("echo-agent")
    assert exit_code == 0


@mock_aws
def test_run_returns_1_when_hash_mismatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", _REGION)

    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(
        Name="/platform/layers/echo-agent/hash",
        Value="stale0000stale00",
        Type="String",
    )

    exit_code = hl.run("echo-agent")
    assert exit_code == 1


def test_run_returns_1_when_aws_region_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    exit_code = hl.main(["echo-agent", "--env", "dev"])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


def test_parse_args_positional_and_env() -> None:
    args = hl.parse_args(["echo-agent", "--env", "dev"])
    assert args.agent_name == "echo-agent"
    assert args.env == "dev"


def test_parse_args_rejects_invalid_env() -> None:
    with pytest.raises(SystemExit):
        hl.parse_args(["echo-agent", "--env", "production"])
