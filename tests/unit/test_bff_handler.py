from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.bff import handler as bff_handler


class FakeContext:
    function_name = "bff"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:bff"
    aws_request_id = "req-123"


def _event(
    *,
    path: str,
    method: str = "POST",
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    tenant_id: str | None = "t-001",
    app_id: str | None = "app-001",
) -> dict[str, object]:
    authorizer = {}
    if tenant_id is not None:
        authorizer["tenantid"] = tenant_id
    if app_id is not None:
        authorizer["appid"] = app_id

    return {
        "httpMethod": method,
        "path": path,
        "headers": headers or {},
        "body": None if body is None else json.dumps(body),
        "requestContext": {
            "authorizer": authorizer,
        },
    }


def _body(response: dict[str, object]) -> dict[str, object]:
    return json.loads(str(response["body"]))


@pytest.fixture(autouse=True)
def reset_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bff_handler, "ENTRA_CLIENT_ID", "client-id")
    monkeypatch.setattr(bff_handler, "ENTRA_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(bff_handler, "ENTRA_TENANT_ID", "tenant-guid")
    monkeypatch.setattr(bff_handler, "ENTRA_TOKEN_ENDPOINT", None)
    monkeypatch.setattr(bff_handler, "ENTRA_AUDIENCE", "api://platform-dev")
    monkeypatch.setattr(bff_handler, "RUNTIME_PING_URL", "http://localhost:8765")


def test_token_refresh_success() -> None:
    event = _event(
        path="/v1/bff/token-refresh",
        body={"scopes": ["api://platform-dev/Agent.Invoke"]},
        headers={"Authorization": "Bearer incoming-user-token"},
    )

    with (
        patch.object(
            bff_handler,
            "_exchange_obo_token",
            return_value={
                "access_token": "new-token",
                "token_type": "Bearer",
                "expires_in": 1800,
                "scope": "api://platform-dev/Agent.Invoke",
            },
        ) as exchange,
        patch.object(
            bff_handler,
            "_now_utc",
            return_value=datetime(2026, 2, 25, 12, 0, 0, tzinfo=UTC),
        ),
    ):
        response = bff_handler.handler(event, FakeContext())

    assert response["statusCode"] == 200
    payload = _body(response)
    assert payload["accessToken"] == "new-token"
    assert payload["tokenType"] == "Bearer"
    assert payload["scope"] == "api://platform-dev/Agent.Invoke"
    assert payload["expiresAt"] == "2026-02-25T12:30:00Z"

    exchange.assert_called_once_with(
        assertion_token="incoming-user-token",
        scopes=["api://platform-dev/Agent.Invoke"],
    )


def test_token_refresh_rejects_explicit_audience() -> None:
    event = _event(
        path="/v1/bff/token-refresh",
        body={
            "scopes": ["api://platform-dev/Agent.Invoke"],
            "audience": "api://platform-dev",
        },
        headers={"Authorization": "Bearer incoming-user-token"},
    )

    response = bff_handler.handler(event, FakeContext())

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert "audience is not supported" in str(error["message"])


def test_exchange_obo_token_uses_only_approved_scopes() -> None:
    with patch.object(bff_handler, "_http_post_form") as http_post:
        bff_handler._exchange_obo_token(
            assertion_token="some-token",
            scopes=["api://platform-dev/Agent.Invoke"],
        )

    http_post.assert_called_once()
    _, params = http_post.call_args[0]
    assert params["scope"] == "api://platform-dev/Agent.Invoke"


def test_token_refresh_rejects_scope_outside_platform_audience() -> None:
    event = _event(
        path="/v1/bff/token-refresh",
        body={"scopes": ["User.Read"]},
        headers={"Authorization": "Bearer incoming-user-token"},
    )

    response = bff_handler.handler(event, FakeContext())

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert "approved platform audience" in str(error["message"])


def test_token_refresh_rejects_default_scope_requests() -> None:
    event = _event(
        path="/v1/bff/token-refresh",
        body={"scopes": ["api://platform-dev/.default"]},
        headers={"Authorization": "Bearer incoming-user-token"},
    )

    response = bff_handler.handler(event, FakeContext())

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert "/.default" in str(error["message"])


def test_token_refresh_requires_authorization_header() -> None:
    event = _event(path="/v1/bff/token-refresh", body={"scopes": ["s"]}, headers={})

    response = bff_handler.handler(event, FakeContext())

    assert response["statusCode"] == 401
    error = _body(response)["error"]
    assert error["code"] == "UNAUTHENTICATED"


def test_token_refresh_rejects_invalid_scopes() -> None:
    event = _event(
        path="/v1/bff/token-refresh",
        body={"scopes": ["", "  "]},
        headers={"Authorization": "Bearer incoming-user-token"},
    )

    response = bff_handler.handler(event, FakeContext())

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "INVALID_REQUEST"


def test_keepalive_success_returns_accepted() -> None:
    event = _event(
        path="/v1/bff/session-keepalive",
        body={"sessionId": "sess-123", "agentName": "echo-agent"},
    )

    with (
        patch.object(bff_handler, "_ping_runtime_session") as ping,
        patch.object(
            bff_handler,
            "_now_utc",
            return_value=datetime(2026, 2, 25, 12, 0, 0, tzinfo=UTC),
        ),
    ):
        response = bff_handler.handler(event, FakeContext())

    assert response["statusCode"] == 202
    payload = _body(response)
    assert payload == {
        "sessionId": "sess-123",
        "status": "accepted",
        "expiresAt": "2026-02-25T12:15:00Z",
    }

    ping.assert_called_once_with(
        tenant_id="t-001",
        app_id="app-001",
        session_id="sess-123",
        agent_name="echo-agent",
    )


def test_keepalive_runtime_unreachable_returns_500() -> None:
    event = _event(
        path="/v1/bff/session-keepalive",
        body={"sessionId": "sess-123", "agentName": "echo-agent"},
    )

    with patch.object(
        bff_handler,
        "_ping_runtime_session",
        side_effect=bff_handler.urllib.error.URLError("down"),
    ):
        response = bff_handler.handler(event, FakeContext())

    assert response["statusCode"] == 500
    error = _body(response)["error"]
    assert error["code"] == "INTERNAL_ERROR"


def test_keepalive_session_not_found_returns_404() -> None:
    event = _event(
        path="/v1/bff/session-keepalive",
        body={"sessionId": "sess-404", "agentName": "echo-agent"},
    )

    # urllib.error.HTTPError is a subclass of URLError
    # Arguments: url, code, msg, hdrs, fp
    mock_http_error = bff_handler.urllib.error.HTTPError(
        "http://localhost:8765/ping",
        404,
        "Not Found",
        {},
        None,
    )

    with patch.object(
        bff_handler,
        "_ping_runtime_session",
        side_effect=mock_http_error,
    ):
        response = bff_handler.handler(event, FakeContext())

    assert response["statusCode"] == 404
    error = _body(response)["error"]
    assert error["code"] == "NOT_FOUND"


def test_keepalive_ping_targets_mock_runtime_contract() -> None:
    with patch.object(bff_handler, "_http_get") as http_get:
        bff_handler._ping_runtime_session(
            tenant_id="t-001",
            app_id="app-001",
            session_id="sess-999",
            agent_name="echo-agent",
        )

    http_get.assert_called_once_with(
        "http://localhost:8765/ping",
        headers={
            "x-tenant-id": "t-001",
            "x-app-id": "app-001",
            "x-session-id": "sess-999",
            "x-agent-name": "echo-agent",
        },
        timeout_seconds=bff_handler.RUNTIME_PING_TIMEOUT_SECONDS,
    )


def test_missing_authorizer_context_returns_401() -> None:
    event = _event(
        path="/v1/bff/token-refresh",
        body={"scopes": ["api://platform-dev/Agent.Invoke"]},
        headers={"Authorization": "Bearer incoming-user-token"},
        tenant_id=None,
        app_id=None,
    )

    response = bff_handler.handler(event, FakeContext())

    assert response["statusCode"] == 401
    error = _body(response)["error"]
    assert error["code"] == "UNAUTHENTICATED"
