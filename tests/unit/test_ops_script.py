"""Unit tests for scripts/ops.py (TASK-029)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request


def _load_ops_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("ops_script", repo_root / "scripts" / "ops.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


ops = _load_ops_module()


def _jwt(payload: dict[str, Any]) -> str:
    header = {"alg": "none", "typ": "JWT"}
    head = (
        ops.base64.urlsafe_b64encode(json.dumps(header).encode("utf-8")).decode("utf-8").rstrip("=")
    )
    body = (
        ops.base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
        .decode("utf-8")
        .rstrip("=")
    )
    return f"{head}.{body}.signature"


class _FakeHeaders:
    def get_content_charset(self, default: str = "utf-8") -> str:
        return default


class _FakeResponse:
    def __init__(self, *, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _write_creds(path: Path, *, env_name: str, token: str, api_base_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    store = {
        "version": 1,
        "profiles": {
            env_name: {
                "accessToken": token,
                "apiBaseUrl": api_base_url,
                "expiresAt": "2099-01-01T00:00:00Z",
            }
        },
    }
    path.write_text(json.dumps(store), encoding="utf-8")


def test_parse_args_top_tenants_defaults() -> None:
    args = ops.parse_args(["top-tenants", "--env", "prod"])
    assert args.command == "top-tenants"
    assert args.env == "prod"
    assert args.n == 10


def test_login_persists_profile(monkeypatch, tmp_path: Path, capsys) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))

    token = _jwt(
        {
            "sub": "abc123",
            "preferred_username": "operator@example.com",
            "roles": ["Platform.Operator"],
            "exp": 4102444800,
        }
    )

    rc = ops.main(
        [
            "login",
            "--env",
            "prod",
            "--api-base-url",
            "https://api.example.com",
            "--token",
            token,
        ]
    )
    assert rc == 0

    payload = json.loads(creds_path.read_text(encoding="utf-8"))
    profile = payload["profiles"]["prod"]
    assert profile["accessToken"] == token
    assert profile["apiBaseUrl"] == "https://api.example.com"
    assert profile["subject"] == "operator@example.com"

    captured = capsys.readouterr()
    assert "Logged in as operator@example.com" in captured.out


def test_top_tenants_calls_expected_endpoint(monkeypatch, tmp_path: Path) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(
        creds_path,
        env_name="dev",
        token="tkn",
        api_base_url="https://api.example.com",
    )

    seen: dict[str, Any] = {}

    def _fake_urlopen(request: Request, timeout: int) -> _FakeResponse:
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["auth"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return _FakeResponse(status=200, payload={"items": []})

    monkeypatch.setattr(ops, "urlopen", _fake_urlopen)

    rc = ops.main(["top-tenants", "--env", "dev", "--n", "5"])
    assert rc == 0
    assert seen["method"] == "GET"
    assert seen["url"] == "https://api.example.com/v1/platform/ops/top-tenants?n=5"
    assert seen["auth"] == "Bearer tkn"
    assert seen["timeout"] == 30


def test_update_tenant_budget_uses_patch_and_json_body(monkeypatch, tmp_path: Path) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(
        creds_path,
        env_name="prod",
        token="budget-token",
        api_base_url="https://ops.example.com",
    )

    seen: dict[str, Any] = {}

    def _fake_urlopen(request: Request, timeout: int) -> _FakeResponse:
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["content_type"] = request.get_header("Content-type")
        seen["timeout"] = timeout
        return _FakeResponse(status=200, payload={"ok": True})

    monkeypatch.setattr(ops, "urlopen", _fake_urlopen)

    rc = ops.main(
        [
            "update-tenant-budget",
            "--env",
            "prod",
            "--tenant",
            "t-123",
            "--budget",
            "5000",
            "--timeout-seconds",
            "12",
        ]
    )
    assert rc == 0
    assert seen["method"] == "PATCH"
    assert seen["url"] == "https://ops.example.com/v1/tenants/t-123"
    assert seen["body"] == {"monthlyBudgetUsd": 5000.0}
    assert seen["content_type"] == "application/json"
    assert seen["timeout"] == 12


def test_api_error_returns_nonzero_and_prints_error(monkeypatch, tmp_path: Path, capsys) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(
        creds_path,
        env_name="dev",
        token="err-token",
        api_base_url="https://api.example.com",
    )

    def _fake_urlopen(request: Request, timeout: int) -> _FakeResponse:
        del timeout
        raise HTTPError(
            url=request.full_url,
            code=403,
            msg="Forbidden",
            hdrs=_FakeHeaders(),
            fp=io.BytesIO(b'{"error":{"code":"FORBIDDEN","message":"nope"}}'),
        )

    monkeypatch.setattr(ops, "urlopen", _fake_urlopen)

    rc = ops.main(["quota-report", "--env", "dev"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "HTTP 403" in captured.err
    assert "FORBIDDEN" in captured.err
