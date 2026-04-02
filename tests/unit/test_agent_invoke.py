"""Unit tests for scripts/agent-invoke.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _load_agent_invoke_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "agent_invoke_script",
        repo_root / "scripts" / "agent-invoke.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


agent_invoke = _load_agent_invoke_module()


class _FakePayloadStream:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw


def test_parse_args_matches_deployed_contract() -> None:
    args = agent_invoke.parse_args(
        [
            "--agent",
            "echo-agent",
            "--tenant",
            "t-test-001",
            "--prompt",
            "hello",
            "--env",
            "staging",
        ]
    )

    assert args.agent == "echo-agent"
    assert args.tenant == "t-test-001"
    assert args.prompt == "hello"
    assert args.env == "staging"
    assert args.mode == "sync"


def test_build_event_matches_bridge_contract() -> None:
    args = agent_invoke.parse_args(
        [
            "--agent",
            "echo-agent",
            "--tenant",
            "t-test-001",
            "--prompt",
            "hello",
            "--session-id",
            "session-123",
            "--webhook-id",
            "wh-123",
        ]
    )

    event = agent_invoke.build_event(args)

    assert event["httpMethod"] == "POST"
    assert event["path"] == "/v1/agents/echo-agent/invoke"
    assert event["pathParameters"] == {"agentName": "echo-agent"}
    assert json.loads(event["body"]) == {
        "input": "hello",
        "sessionId": "session-123",
        "webhookId": "wh-123",
    }
    assert event["requestContext"]["authorizer"]["lambda"]["tenantid"] == "t-test-001"


def test_main_rejects_local_env_with_dev_invoke_guidance(capsys) -> None:
    rc = agent_invoke.main(
        [
            "--agent",
            "echo-agent",
            "--tenant",
            "t-test-001",
            "--env",
            "local",
        ]
    )

    assert rc == 1
    captured = capsys.readouterr()
    assert "make dev-invoke" in captured.err


def test_invoke_remote_targets_bridge_lambda(monkeypatch, capsys) -> None:
    seen: dict[str, Any] = {}

    class _FakeLambdaClient:
        def invoke(self, **kwargs: Any) -> dict[str, Any]:
            seen.update(kwargs)
            return {
                "Payload": _FakePayloadStream(
                    {"statusCode": 200, "body": json.dumps({"result": "ok"})}
                )
            }

    monkeypatch.setattr(agent_invoke.boto3, "client", lambda service: _FakeLambdaClient())

    rc = agent_invoke.main(
        [
            "--agent",
            "echo-agent",
            "--tenant",
            "t-test-001",
            "--prompt",
            "hello world",
            "--env",
            "dev",
        ]
    )

    assert rc == 0
    assert seen["FunctionName"] == "platform-dev-bridge"
    assert seen["InvocationType"] == "RequestResponse"
    payload = json.loads(seen["Payload"].decode("utf-8"))
    assert payload["path"] == "/v1/agents/echo-agent/invoke"
    assert json.loads(payload["body"]) == {"input": "hello world"}
    captured = capsys.readouterr()
    assert '"statusCode": 200' in captured.out
