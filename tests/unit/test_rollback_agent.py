"""Unit tests for rollback_agent.py."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import boto3
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rollback_agent = _load_module("rollback_agent")

_REGION = "eu-west-2"


@mock_aws
def test_rollback_agent_success(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("CI", "true")

    agent_name = "test-agent"
    env = "dev"

    # Setup SSM with current state (pointing to v1.1.0)
    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(
        Name=f"/platform/agents/{env}/{agent_name}/latest-version", Value="1.1.0", Type="String"
    )

    def fake_request_api(url, method, token, body=None):
        if method == "GET" and url.endswith("/v1/platform/agents"):
            return {
                "items": [
                    {"agent_name": agent_name, "version": "1.0.0", "status": "released"},
                    {"agent_name": agent_name, "version": "1.1.0", "status": "released"},
                ]
            }
        if method == "PATCH" and f"/v1/platform/agents/{agent_name}/versions/1.1.0" in url:
            return {"status": "updated"}
        return {}

    monkeypatch.setattr(rollback_agent, "_request_api", fake_request_api)
    monkeypatch.setenv("API_BASE_URL", "http://localhost")
    monkeypatch.setenv("PLATFORM_ACCESS_TOKEN", "fake-token")

    # Run Rollback
    success = rollback_agent.rollback_agent(agent_name, env, None, None)
    assert success is True

    # Verify SSM points back to v1.0.0
    latest_version_param = ssm.get_parameter(
        Name=f"/platform/agents/{env}/{agent_name}/latest-version"
    )
    assert latest_version_param["Parameter"]["Value"] == "1.0.0"


@mock_aws
def test_rollback_agent_fails_no_previous(monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    agent_name = "test-agent"
    env = "dev"

    def fake_request_api(url, method, token, body=None):
        if method == "GET" and url.endswith("/v1/platform/agents"):
            return {
                "items": [
                    {"agent_name": agent_name, "version": "1.0.0", "status": "released"},
                ]
            }
        return {}

    monkeypatch.setattr(rollback_agent, "_request_api", fake_request_api)
    monkeypatch.setenv("API_BASE_URL", "http://localhost")
    monkeypatch.setenv("PLATFORM_ACCESS_TOKEN", "fake-token")

    # Run Rollback - should fail
    success = rollback_agent.rollback_agent(agent_name, env, None, None)
    assert success is False
