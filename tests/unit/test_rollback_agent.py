import importlib.util
import sys
from pathlib import Path
from typing import Any

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


def test_rollback_agent_success(monkeypatch):
    agent_name = "test-agent"
    env = "dev"
    seen: dict[str, Any] = {}

    def fake_request_api(url, method, token, body=None):
        if method == "GET" and url.endswith("/v1/platform/agents"):
            seen["list_url"] = url
            return {
                "items": [
                    {"agent_name": agent_name, "version": "1.0.0", "status": "promoted"},
                    {"agent_name": agent_name, "version": "1.1.0", "status": "promoted"},
                ]
            }
        if method == "PATCH" and f"/v1/platform/agents/{agent_name}/versions/1.1.0" in url:
            seen["patch_url"] = url
            seen["patch_body"] = body
            return {"status": "updated"}
        return {}

    monkeypatch.setattr(rollback_agent, "_request_api", fake_request_api)
    monkeypatch.setenv("API_BASE_URL", "http://localhost")
    monkeypatch.setenv("PLATFORM_ACCESS_TOKEN", "fake-token")

    # Run Rollback
    success = rollback_agent.rollback_agent(
        agent_name, env, None, None, notes="operator rollback evidence"
    )
    assert success is True
    assert seen["list_url"] == "http://localhost/v1/platform/agents"
    assert seen["patch_url"] == f"http://localhost/v1/platform/agents/{agent_name}/versions/1.1.0"
    assert seen["patch_body"] == {
        "status": "rolled_back",
        "releaseNotes": "operator rollback evidence",
    }


def test_rollback_agent_fails_no_previous(monkeypatch):
    agent_name = "test-agent"
    env = "dev"

    def fake_request_api(url, method, token, body=None):
        if method == "GET" and url.endswith("/v1/platform/agents"):
            return {
                "items": [
                    {"agent_name": agent_name, "version": "1.0.0", "status": "promoted"},
                ]
            }
        return {}

    monkeypatch.setattr(rollback_agent, "_request_api", fake_request_api)
    monkeypatch.setenv("API_BASE_URL", "http://localhost")
    monkeypatch.setenv("PLATFORM_ACCESS_TOKEN", "fake-token")

    # Run Rollback - should fail
    success = rollback_agent.rollback_agent(agent_name, env, None, None)
    assert success is False
