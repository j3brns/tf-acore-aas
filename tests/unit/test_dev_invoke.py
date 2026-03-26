"""Unit tests for scripts/dev-invoke.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request


def _load_dev_invoke_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "dev_invoke_script",
        repo_root / "scripts" / "dev-invoke.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


dev_invoke = _load_dev_invoke_module()


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int,
        payload: Any,
        content_type: str = "application/json",
    ) -> None:
        self.status = status
        self._payload = payload
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def read(self) -> bytes:
        if isinstance(self._payload, bytes):
            return self._payload
        return json.dumps(self._payload).encode("utf-8")


def test_parse_args_matches_makefile_contract() -> None:
    args = dev_invoke.parse_args(
        [
            "--agent",
            "echo-agent",
            "--tenant",
            "t-basic-001",
            "--jwt",
            "jwt-token",
            "--prompt",
            "Hello from local environment",
            "--mode",
            "sync",
        ]
    )

    assert args.agent == "echo-agent"
    assert args.tenant == "t-basic-001"
    assert args.token == "jwt-token"
    assert args.prompt == "Hello from local environment"
    assert args.mode == "sync"
    assert args.env == "local"


def test_build_request_includes_contract_headers_and_payload() -> None:
    args = dev_invoke.parse_args(
        [
            "--agent",
            "echo-agent",
            "--tenant",
            "t-basic-001",
            "--prompt",
            "Hello",
            "--mode",
            "streaming",
            "--session-id",
            "session-123",
            "--webhook-id",
            "wh-123",
        ]
    )

    request = dev_invoke.build_request(
        api_base_url="http://localhost:8080",
        token="jwt-token",
        args=args,
    )

    assert request.full_url == "http://localhost:8080/v1/agents/echo-agent/invoke"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer jwt-token"
    assert request.get_header("Accept") == "text/event-stream"
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["x-tenant-id"] == "t-basic-001"
    assert json.loads(request.data.decode("utf-8")) == {
        "input": "Hello",
        "sessionId": "session-123",
        "webhookId": "wh-123",
    }


def test_resolve_token_uses_bootstrap_fixture_aliases_for_known_tenant(
    monkeypatch, tmp_path: Path
) -> None:
    env_test = tmp_path / ".env.test"
    env_test.write_text(
        "\n".join(
            [
                "BASIC_TENANT_ID=t-basic-001",
                "TEST_JWT_BASIC=fixture-basic-token",  # pragma: allowlist secret
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dev_invoke, "_repo_root", lambda: tmp_path)

    token = dev_invoke._resolve_token(None, "t-basic-001", "local")

    assert token == "fixture-basic-token"


def test_main_documented_dev_invoke_path_uses_local_defaults(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    env_test = tmp_path / ".env.test"
    env_test.write_text(
        "\n".join(
            [
                "BASIC_TENANT_ID=t-basic-001",
                "BASIC_TENANT_JWT=fixture-basic-token",  # pragma: allowlist secret
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dev_invoke, "_repo_root", lambda: tmp_path)

    seen: dict[str, Any] = {}

    def _fake_urlopen(request: Request, timeout: int) -> _FakeResponse:
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["accept"] = request.get_header("Accept")
        seen["timeout"] = timeout
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(
            status=200,
            payload={"status": "success", "output": "Echo: Hello from local environment"},
        )

    monkeypatch.setattr(dev_invoke, "urlopen", _fake_urlopen)

    rc = dev_invoke.main(
        [
            "--agent",
            "echo-agent",
            "--tenant",
            "t-basic-001",
            "--prompt",
            "Hello from local environment",
            "--mode",
            "sync",
        ]
    )

    assert rc == 0
    assert seen["url"] == "http://localhost:8080/v1/agents/echo-agent/invoke"
    assert seen["auth"] == "Bearer fixture-basic-token"
    assert seen["accept"] == "application/json"
    assert seen["timeout"] == dev_invoke.DEFAULT_TIMEOUT_SECONDS
    assert seen["body"] == {"input": "Hello from local environment"}
    captured = capsys.readouterr()
    assert "Echo: Hello from local environment" in captured.out


def test_resolve_token_supports_direct_bootstrap_alias_without_tenant_id_mapping(
    monkeypatch, tmp_path: Path
) -> None:
    env_test = tmp_path / ".env.test"
    env_test.write_text(
        "TEST_JWT_PREMIUM=fixture-premium-token\n",  # pragma: allowlist secret
        encoding="utf-8",
    )
    monkeypatch.setattr(dev_invoke, "_repo_root", lambda: tmp_path)

    token = dev_invoke._resolve_token(None, "t-premium-001", "local")

    assert token == "fixture-premium-token"


def test_main_fails_cleanly_when_token_cannot_be_resolved(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(dev_invoke, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(tmp_path / "missing-creds.json"))

    rc = dev_invoke.main(
        [
            "--agent",
            "echo-agent",
            "--tenant",
            "t-missing",
            "--prompt",
            "hello",
        ]
    )

    assert rc == 1
    captured = capsys.readouterr()
    assert "Bearer token not set" in captured.err
