import os
from unittest.mock import MagicMock, patch

import jwt
import pytest

from gateway.interceptors import request_interceptor

OS_ENV = {
    "AWS_REGION": "eu-west-2",
    "ENTRA_JWKS_URL": "http://localhost:8766/.well-known/jwks.json",
    "ENTRA_AUDIENCE": "api://platform-local",
    "ENTRA_ISSUER": "http://localhost:8766",
    "TOOLS_TABLE": "platform-tools-dev",
    "SCOPED_TOKEN_SIGNING_KEY": "unit-test-signing-key-with-32-bytes-minimum",
    "SCOPED_TOKEN_ISSUER": "platform-gateway",
    "PLATFORM_ENV": "local",
}


@pytest.fixture
def mock_env():
    with patch.dict(os.environ, OS_ENV, clear=False):
        yield


@pytest.fixture(autouse=True)
def patch_module_constants():
    with (
        patch("gateway.interceptors.request_interceptor.ENTRA_JWKS_URL", OS_ENV["ENTRA_JWKS_URL"]),
        patch("gateway.interceptors.request_interceptor.ENTRA_AUDIENCE", OS_ENV["ENTRA_AUDIENCE"]),
        patch("gateway.interceptors.request_interceptor.ENTRA_ISSUER", OS_ENV["ENTRA_ISSUER"]),
        patch("gateway.interceptors.request_interceptor.TOOLS_TABLE", OS_ENV["TOOLS_TABLE"]),
        patch(
            "gateway.interceptors.request_interceptor.SCOPED_TOKEN_ISSUER",
            OS_ENV["SCOPED_TOKEN_ISSUER"],
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def reset_globals():
    request_interceptor._jwk_client = None
    request_interceptor._dynamodb_resource = None
    request_interceptor._idempotency_handler = None
    request_interceptor._idempotency_handler_table = None
    request_interceptor._warned_fallback_signing_key = False
    yield


class MockContext:
    function_name = "request-interceptor"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:eu-west-2:000000000000:function:request-interceptor"
    aws_request_id = "request-id"


@pytest.fixture
def lambda_context():
    return MockContext()


def _base_event() -> dict:
    return {
        "interceptorInputVersion": "1.0",
        "mcp": {
            "gatewayRequest": {
                "path": "/mcp",
                "httpMethod": "POST",
                "headers": {
                    "Authorization": "Bearer original.user.token",
                    "Mcp-Session-Id": "mcp-session-123",
                    "Accept": "application/json",
                },
                "body": {
                    "jsonrpc": "2.0",
                    "id": "rpc-1",
                    "method": "tools/call",
                    "params": {"name": "echo", "arguments": {"text": "hello"}},
                },
            }
        },
    }


def _valid_payload() -> dict:
    return {
        "tenantid": "t-basic-001",
        "appid": "platform-local",
        "tier": "basic",
        "sub": "user-123",
        "iss": OS_ENV["ENTRA_ISSUER"],
        "aud": OS_ENV["ENTRA_AUDIENCE"],
    }


@patch("gateway.interceptors.request_interceptor.get_jwk_client")
@patch("gateway.interceptors.request_interceptor.get_tool_record")
def test_request_interceptor_enforces_tier(
    mock_get_tool_record, mock_get_jwk_client, mock_env, lambda_context
):
    event = _base_event()
    payload = _valid_payload()
    mock_get_tool_record.return_value = {
        "tool_name": "echo",
        "tier_minimum": "premium",
        "enabled": True,
    }
    mock_get_jwk_client.return_value = MagicMock(
        get_signing_key_from_jwt=MagicMock(return_value=MagicMock(key="pub-key"))
    )

    with patch("jwt.decode", return_value=payload):
        result = request_interceptor.handler(event, lambda_context)

    response = result["mcp"]["transformedGatewayResponse"]
    assert response["statusCode"] == 403
    assert response["body"]["error"]["message"] == "Tenant tier is insufficient for this tool"
    mock_get_tool_record.assert_called_once_with("echo", "t-basic-001")


@patch("gateway.interceptors.request_interceptor.get_jwk_client")
@patch("gateway.interceptors.request_interceptor.get_tool_record")
def test_request_interceptor_issues_scoped_token_and_headers(
    mock_get_tool_record, mock_get_jwk_client, mock_env, lambda_context
):
    event = _base_event()
    payload = _valid_payload()
    mock_get_tool_record.return_value = {
        "tool_name": "echo",
        "tier_minimum": "basic",
        "enabled": True,
    }
    mock_get_jwk_client.return_value = MagicMock(
        get_signing_key_from_jwt=MagicMock(return_value=MagicMock(key="pub-key"))
    )

    with patch("jwt.decode", return_value=payload):
        result = request_interceptor.handler(event, lambda_context)

    transformed = result["mcp"]["transformedGatewayRequest"]
    headers = transformed["headers"]
    assert headers["x-tenant-id"] == "t-basic-001"
    assert headers["x-app-id"] == "platform-local"
    assert headers["x-tier"] == "basic"
    assert headers["x-acting-sub"] == "user-123"

    auth_header = headers["Authorization"]
    assert auth_header.startswith("Bearer ")
    assert auth_header != "Bearer original.user.token"

    scoped_token = auth_header.split(" ", 1)[1]
    scoped_claims = jwt.decode(
        scoped_token,
        OS_ENV["SCOPED_TOKEN_SIGNING_KEY"],
        algorithms=["HS256"],
        options={"verify_aud": False},
    )
    assert scoped_claims["tenantid"] == "t-basic-001"
    assert scoped_claims["appid"] == "platform-local"
    assert scoped_claims["tier"] == "basic"
    assert scoped_claims["acting_sub"] == "user-123"
    assert scoped_claims["scope_tool"] == "echo"
    assert scoped_claims["aud"] == "tool:echo"
    assert scoped_claims["exp"] > scoped_claims["iat"]
    assert scoped_claims["exp"] - scoped_claims["iat"] <= 300


@patch("gateway.interceptors.request_interceptor.get_jwk_client")
def test_request_interceptor_rejects_invalid_jwt(mock_get_jwk_client, mock_env, lambda_context):
    event = _base_event()
    mock_get_jwk_client.return_value = MagicMock(
        get_signing_key_from_jwt=MagicMock(return_value=MagicMock(key="pub-key"))
    )

    with patch("jwt.decode", side_effect=jwt.InvalidTokenError("bad token")):
        result = request_interceptor.handler(event, lambda_context)

    response = result["mcp"]["transformedGatewayResponse"]
    assert response["statusCode"] == 401
    assert response["body"]["error"]["message"] == "Bearer token validation failed"


@patch("gateway.interceptors.request_interceptor.get_jwk_client")
@patch("gateway.interceptors.request_interceptor.get_tool_record")
def test_request_interceptor_enforces_tier_minimum_camel_case(
    mock_get_tool_record, mock_get_jwk_client, mock_env, lambda_context
):
    event = _base_event()
    mock_get_tool_record.return_value = {
        "tool_name": "echo",
        "tierMinimum": "premium",
        "enabled": True,
    }
    mock_get_jwk_client.return_value = MagicMock(
        get_signing_key_from_jwt=MagicMock(return_value=MagicMock(key="pub-key"))
    )

    with patch("jwt.decode", return_value=_valid_payload()):
        result = request_interceptor.handler(event, lambda_context)

    response = result["mcp"]["transformedGatewayResponse"]
    assert response["statusCode"] == 403
    assert response["body"]["error"]["message"] == "Tenant tier is insufficient for this tool"


def test_request_interceptor_uses_idempotency_key_for_duplicates(lambda_context):
    event = _base_event()
    seen_keys: list[str] = []
    cache: dict[str, dict] = {}

    def fake_idempotency_handler(
        *, idempotency_data: dict[str, str], interceptor_event: dict
    ) -> dict:
        key = idempotency_data["idempotency_key"]
        seen_keys.append(key)
        if key not in cache:
            cache[key] = request_interceptor._process_request(interceptor_event)
        return cache[key]

    with (
        patch(
            "gateway.interceptors.request_interceptor._get_idempotency_handler",
            return_value=fake_idempotency_handler,
        ),
        patch(
            "gateway.interceptors.request_interceptor._process_request",
            return_value={"result": "ok"},
        ) as mock_process,
    ):
        first = request_interceptor.handler(event, lambda_context)
        second = request_interceptor.handler(event, lambda_context)

    assert first == {"result": "ok"}
    assert second == {"result": "ok"}
    assert seen_keys == ["mcp-session-123:rpc-1", "mcp-session-123:rpc-1"]
    mock_process.assert_called_once_with(event)
