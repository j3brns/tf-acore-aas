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

import pytest


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


def _write_failover_lock_token(path: Path, *, lock_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "lockId": lock_id,
        "tableName": "platform-ops-locks",
        "lockName": "platform-runtime-failover",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


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


def test_set_runtime_region_uses_failover_api_contract(monkeypatch, tmp_path: Path) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    token_path = tmp_path / ".build" / "failover-lock-token.json"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    monkeypatch.setenv("FAILOVER_LOCK_TOKEN_PATH", str(token_path))
    _write_creds(
        creds_path,
        env_name="prod",
        token="failover-token",
        api_base_url="https://ops.example.com",
    )
    _write_failover_lock_token(token_path, lock_id="lock-123")

    seen: dict[str, Any] = {}

    def _fake_urlopen(request: Request, timeout: int) -> _FakeResponse:
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _FakeResponse(status=200, payload={"status": "completed"})

    monkeypatch.setattr(ops, "urlopen", _fake_urlopen)

    rc = ops.main(["set-runtime-region", "--env", "prod", "--region", "eu-central-1"])

    assert rc == 0
    assert seen["method"] == "POST"
    assert seen["url"] == "https://ops.example.com/v1/platform/failover"
    assert seen["body"] == {"targetRegion": "eu-central-1", "lockId": "lock-123"}
    assert seen["timeout"] == 30


def test_set_runtime_region_accepts_explicit_lock_id(monkeypatch, tmp_path: Path) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(
        creds_path,
        env_name="prod",
        token="failover-token",
        api_base_url="https://ops.example.com",
    )

    seen: dict[str, Any] = {}

    def _fake_urlopen(request: Request, timeout: int) -> _FakeResponse:
        del timeout
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(status=200, payload={"status": "completed"})

    monkeypatch.setattr(ops, "urlopen", _fake_urlopen)

    rc = ops.main(
        [
            "set-runtime-region",
            "--env",
            "prod",
            "--region",
            "eu-central-1",
            "--lock-id",
            "lock-explicit",
        ]
    )

    assert rc == 0
    assert seen["body"] == {"targetRegion": "eu-central-1", "lockId": "lock-explicit"}


def test_set_runtime_region_requires_lock_id_when_no_saved_token(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    token_path = tmp_path / ".build" / "failover-lock-token.json"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    monkeypatch.setenv("FAILOVER_LOCK_TOKEN_PATH", str(token_path))
    _write_creds(
        creds_path,
        env_name="prod",
        token="failover-token",
        api_base_url="https://ops.example.com",
    )

    rc = ops.main(["set-runtime-region", "--env", "prod", "--region", "eu-central-1"])

    assert rc == 2
    assert "Failover lock id required" in capsys.readouterr().err


def test_parse_args_rejects_removed_failover_lock_api_commands() -> None:
    with pytest.raises(SystemExit):
        ops.parse_args(["failover-lock-acquire"])

    with pytest.raises(SystemExit):
        ops.parse_args(["failover-lock-release"])


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


def test_lambda_rollback_calls_expected_endpoint(monkeypatch, tmp_path: Path) -> None:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(
        creds_path,
        env_name="prod",
        token="rollback-token",
        api_base_url="https://ops.example.com",
    )

    seen: dict[str, Any] = {}

    def _fake_urlopen(request: Request, timeout: int) -> _FakeResponse:
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _FakeResponse(status=200, payload={"status": "rolled_back"})

    monkeypatch.setattr(ops, "urlopen", _fake_urlopen)

    rc = ops.main(
        [
            "lambda-rollback",
            "--env",
            "prod",
            "--function",
            "bridge",
            "--alias",
            "live",
        ]
    )
    assert rc == 0
    assert seen["method"] == "POST"
    assert seen["url"] == "https://ops.example.com/v1/platform/ops/lambda-rollback"
    assert seen["body"] == {"functionSuffix": "bridge", "aliasName": "live"}


# ---------------------------------------------------------------------------
# Additional command endpoint coverage
# ---------------------------------------------------------------------------


def _make_creds(monkeypatch: Any, tmp_path: Path) -> Path:
    creds_path = tmp_path / ".platform" / "credentials"
    monkeypatch.setenv("PLATFORM_CREDENTIALS_PATH", str(creds_path))
    _write_creds(creds_path, env_name="dev", token="tk", api_base_url="https://api.example.com")
    return creds_path


def _capture_request(monkeypatch: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def _fake_urlopen(request: Request, timeout: int) -> _FakeResponse:
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["timeout"] = timeout
        data = request.data
        if data:
            seen["body"] = json.loads(data.decode("utf-8"))
        return _FakeResponse(status=200, payload={})

    monkeypatch.setattr(ops, "urlopen", _fake_urlopen)
    return seen


def test_suspend_tenant_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["suspend-tenant", "--env", "dev", "--tenant", "t-abc", "--reason", "abuse"])

    assert rc == 0
    assert seen["method"] == "POST"
    assert "/v1/platform/ops/tenants/t-abc/suspend" in seen["url"]
    assert seen["body"] == {"reason": "abuse"}


def test_reinstate_tenant_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["reinstate-tenant", "--env", "dev", "--tenant", "t-xyz"])

    assert rc == 0
    assert seen["method"] == "POST"
    assert "/v1/platform/ops/tenants/t-xyz/reinstate" in seen["url"]


def test_tenant_sessions_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["tenant-sessions", "--env", "dev", "--tenant", "t-sessions"])

    assert rc == 0
    assert seen["method"] == "GET"
    assert "/v1/platform/ops/tenants/t-sessions/sessions" in seen["url"]


def test_invocation_report_calls_correct_endpoint_with_days(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["invocation-report", "--env", "dev", "--tenant", "t-inv", "--days", "14"])

    assert rc == 0
    assert seen["method"] == "GET"
    assert "/v1/platform/ops/tenants/t-inv/invocations" in seen["url"]
    assert "days=14" in seen["url"]


def test_security_events_calls_correct_endpoint_with_hours(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["security-events", "--env", "dev", "--hours", "48"])

    assert rc == 0
    assert seen["method"] == "GET"
    assert "/v1/platform/ops/security-events" in seen["url"]
    assert "hours=48" in seen["url"]


def test_dlq_inspect_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["dlq-inspect", "--env", "dev", "--queue", "bridge-dlq"])

    assert rc == 0
    assert seen["method"] == "GET"
    assert "/v1/platform/ops/dlq/bridge-dlq" in seen["url"]


def test_dlq_redrive_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["dlq-redrive", "--env", "dev", "--queue", "bridge-dlq"])

    assert rc == 0
    assert seen["method"] == "POST"
    assert "/v1/platform/ops/dlq/bridge-dlq/redrive" in seen["url"]


def test_error_rate_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["error-rate", "--env", "dev", "--minutes", "15"])

    assert rc == 0
    assert seen["method"] == "GET"
    assert "minutes=15" in seen["url"]


def test_notify_tenant_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(
        ["notify-tenant", "--env", "dev", "--tenant", "t-notify", "--template", "budget_exceeded"]
    )

    assert rc == 0
    assert seen["method"] == "POST"
    assert "/v1/platform/ops/tenants/t-notify/notify" in seen["url"]
    assert seen["body"] == {"template": "budget_exceeded"}


def test_audit_export_with_date_range(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(
        [
            "audit-export",
            "--env",
            "dev",
            "--tenant",
            "t-audit",
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-31",
        ]
    )

    assert rc == 0
    assert seen["method"] == "GET"
    assert "/v1/tenants/t-audit/audit-export" in seen["url"]
    assert "start=2026-01-01" in seen["url"]
    assert "end=2026-01-31" in seen["url"]


def test_audit_export_without_date_range(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["audit-export", "--env", "dev", "--tenant", "t-audit"])

    assert rc == 0
    assert "start" not in seen["url"]
    assert "end" not in seen["url"]


def test_fail_job_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(["fail-job", "--env", "dev", "--job", "job-001", "--reason", "timed out"])

    assert rc == 0
    assert seen["method"] == "POST"
    assert "/v1/platform/ops/jobs/job-001/fail" in seen["url"]
    assert seen["body"] == {"reason": "timed out"}


def test_page_security_calls_correct_endpoint(monkeypatch, tmp_path: Path) -> None:
    _make_creds(monkeypatch, tmp_path)
    seen = _capture_request(monkeypatch)

    rc = ops.main(
        [
            "page-security",
            "--env",
            "dev",
            "--incident",
            "INC-001",
            "--tenant",
            "t-vuln",
        ]
    )

    assert rc == 0
    assert seen["method"] == "POST"
    assert "/v1/platform/ops/security/page" in seen["url"]
    assert seen["body"] == {"incident": "INC-001", "tenantId": "t-vuln"}


# ---------------------------------------------------------------------------
# _resolve_token and _resolve_api_base_url unit tests
# ---------------------------------------------------------------------------


def test_resolve_token_prefers_cli_token() -> None:
    token = ops._resolve_token("cli-token", {"accessToken": "profile-token"})
    assert token == "cli-token"


def test_resolve_token_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("OPS_ACCESS_TOKEN", "env-token")
    token = ops._resolve_token(None, {})
    assert token == "env-token"


def test_resolve_token_falls_back_to_profile() -> None:
    profile = {"accessToken": "stored-token", "expiresAt": "2099-01-01T00:00:00Z"}
    token = ops._resolve_token(None, profile)
    assert token == "stored-token"


def test_resolve_token_rejects_expired_profile_token() -> None:
    profile = {"accessToken": "old-token", "expiresAt": "2020-01-01T00:00:00Z"}
    with pytest.raises(ops.OpsCliError, match="expired"):
        ops._resolve_token(None, profile)


def test_resolve_token_no_token_raises() -> None:
    with pytest.raises(ops.OpsCliError, match="No access token"):
        ops._resolve_token(None, {})


def test_resolve_api_base_url_prefers_explicit() -> None:
    url = ops._resolve_api_base_url(explicit="https://explicit.example.com", profile={})
    assert url == "https://explicit.example.com"


def test_resolve_api_base_url_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("API_BASE_URL", "https://env.example.com")
    url = ops._resolve_api_base_url(explicit=None, profile={})
    assert url == "https://env.example.com"


def test_resolve_api_base_url_uses_profile() -> None:
    profile = {"apiBaseUrl": "https://profile.example.com"}
    url = ops._resolve_api_base_url(explicit=None, profile=profile)
    assert url == "https://profile.example.com"


def test_resolve_api_base_url_raises_when_no_source(monkeypatch) -> None:
    monkeypatch.delenv("API_BASE_URL", raising=False)
    monkeypatch.delenv("VITE_API_BASE_URL", raising=False)
    with pytest.raises(ops.OpsCliError, match="API base URL not set"):
        ops._resolve_api_base_url(explicit=None, profile={})


# ---------------------------------------------------------------------------
# _jwt_payload, _token_subject, _token_roles
# ---------------------------------------------------------------------------


def test_jwt_payload_extracts_claims() -> None:
    token = _jwt({"sub": "u1", "roles": ["Admin"]})
    claims = ops._jwt_payload(token)
    assert claims["sub"] == "u1"
    assert claims["roles"] == ["Admin"]


def test_jwt_payload_invalid_token_returns_empty() -> None:
    assert ops._jwt_payload("not.a.valid") == {}


def test_token_subject_preferred_username_takes_priority() -> None:
    claims = {"preferred_username": "ops@example.com", "sub": "abc"}
    assert ops._token_subject(claims) == "ops@example.com"


def test_token_subject_falls_back_to_sub() -> None:
    claims = {"sub": "abc123"}
    assert ops._token_subject(claims) == "abc123"


def test_token_subject_unknown_when_empty() -> None:
    assert ops._token_subject({}) == "unknown"


def test_token_roles_list() -> None:
    claims = {"roles": ["Platform.Operator", "Platform.Admin"]}
    assert ops._token_roles(claims) == ["Platform.Operator", "Platform.Admin"]


def test_token_roles_string_format() -> None:
    claims = {"roles": "Platform.Operator Platform.Admin"}
    roles = ops._token_roles(claims)
    assert "Platform.Operator" in roles
    assert "Platform.Admin" in roles


def test_token_roles_empty_when_missing() -> None:
    assert ops._token_roles({}) == []


# ---------------------------------------------------------------------------
# _build_url
# ---------------------------------------------------------------------------


def test_build_url_without_query() -> None:
    url = ops._build_url("https://api.example.com", "/v1/tenants", None)
    assert url == "https://api.example.com/v1/tenants"


def test_build_url_with_query() -> None:
    url = ops._build_url("https://api.example.com", "/v1/tenants", {"n": "5"})
    assert url == "https://api.example.com/v1/tenants?n=5"


def test_build_url_trailing_slash_in_base() -> None:
    url = ops._build_url("https://api.example.com/", "/v1/resource", None)
    assert url == "https://api.example.com/v1/resource"
