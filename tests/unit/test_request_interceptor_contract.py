from unittest.mock import patch

from gateway.interceptors import request_interceptor


def _base_event():
    return {
        "interceptorInputVersion": "1.0",
        "mcp": {
            "gatewayRequest": {
                "headers": {"Authorization": "Bearer token", "Mcp-Session-Id": "sess-1"},
                "body": {"id": "rpc-1", "method": "tools/call", "params": {"name": "echo"}},
            }
        },
    }


class _Ctx:
    function_name = "request-interceptor"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:eu-west-2:000000000000:function:request-interceptor"
    aws_request_id = "request-id"


def test_request_interceptor_auth_matrix_rejects_missing_or_wrong_scheme():
    missing_auth_event = _base_event()
    missing_auth_event["mcp"]["gatewayRequest"]["headers"].pop("Authorization")
    basic_auth_event = _base_event()
    basic_auth_event["mcp"]["gatewayRequest"]["headers"]["Authorization"] = "Basic abc"

    missing = request_interceptor.handler(missing_auth_event, _Ctx())
    wrong_scheme = request_interceptor.handler(basic_auth_event, _Ctx())

    assert missing["mcp"]["transformedGatewayResponse"]["statusCode"] == 401
    assert wrong_scheme["mcp"]["transformedGatewayResponse"]["statusCode"] == 401


def test_request_interceptor_bypasses_idempotency_when_key_missing():
    event = _base_event()
    event["mcp"]["gatewayRequest"]["headers"].pop("Mcp-Session-Id")
    with (
        patch(
            "gateway.interceptors.request_interceptor._get_idempotency_handler",
            return_value=lambda **_: {"result": "bad"},
        ),
        patch(
            "gateway.interceptors.request_interceptor._process_request",
            return_value={"result": "ok"},
        ) as process_request,
    ):
        result = request_interceptor.handler(event, _Ctx())
    assert result == {"result": "ok"}
    process_request.assert_called_once_with(event)
