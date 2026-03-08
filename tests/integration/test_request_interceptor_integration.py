import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from gateway.interceptors import request_interceptor

FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "request_interceptor_gateway_event.json"
)
OS_ENV = {
    "AWS_REGION": "eu-west-2",
    "ENTRA_JWKS_URL": "http://localhost:8766/.well-known/jwks.json",
    "ENTRA_AUDIENCE": "api://platform-local",
    "ENTRA_ISSUER": "http://localhost:8766",
    "TOOLS_TABLE": "platform-tools-dev",
    "SCOPED_TOKEN_SIGNING_KEY": "unit-test-signing-key-with-32-bytes-minimum",
    "SCOPED_TOKEN_ISSUER": "platform-gateway",
}


class MockContext:
    function_name = "request-interceptor"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:eu-west-2:000000000000:function:request-interceptor"
    aws_request_id = "request-id"


def test_request_interceptor_gateway_event_fixture_roundtrip():
    event = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload = {
        "tenantid": "t-basic-001",
        "appid": "platform-local",
        "tier": "basic",
        "sub": "user-123",
        "iss": OS_ENV["ENTRA_ISSUER"],
        "aud": OS_ENV["ENTRA_AUDIENCE"],
    }
    mock_jwk_client = MagicMock(
        get_signing_key_from_jwt=MagicMock(return_value=MagicMock(key="pub-key"))
    )

    with (
        patch.dict(os.environ, OS_ENV, clear=False),
        patch("gateway.interceptors.request_interceptor.ENTRA_JWKS_URL", OS_ENV["ENTRA_JWKS_URL"]),
        patch("gateway.interceptors.request_interceptor.ENTRA_AUDIENCE", OS_ENV["ENTRA_AUDIENCE"]),
        patch("gateway.interceptors.request_interceptor.ENTRA_ISSUER", OS_ENV["ENTRA_ISSUER"]),
        patch(
            "gateway.interceptors.request_interceptor.SCOPED_TOKEN_ISSUER",
            OS_ENV["SCOPED_TOKEN_ISSUER"],
        ),
        patch(
            "gateway.interceptors.request_interceptor.get_jwk_client",
            return_value=mock_jwk_client,
        ),
        patch(
            "gateway.interceptors.request_interceptor.get_tool_record",
            return_value={"tool_name": "echo", "tierMinimum": "basic", "enabled": True},
        ),
        patch("jwt.decode", return_value=payload),
    ):
        result = request_interceptor.handler(event, MockContext())

    transformed_request = result["mcp"]["transformedGatewayRequest"]
    headers = transformed_request["headers"]

    assert transformed_request["body"]["id"] == "rpc-42"
    assert transformed_request["body"]["params"]["name"] == "echo"
    assert headers["x-tenant-id"] == "t-basic-001"
    assert headers["x-app-id"] == "platform-local"
    assert headers["x-tier"] == "basic"
    assert headers["x-acting-sub"] == "user-123"
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["Authorization"] != "Bearer original.user.token"
