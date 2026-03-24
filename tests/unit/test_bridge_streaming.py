import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))


from data_access.models import AgentRecord, InvocationMode, TenantContext

from src.bridge.handler import _send_streaming_response, handle_streaming_invocation, handler


class FakeLambdaContext:
    def __init__(self):
        self.aws_request_id = "test-request-id"
        self.function_name = "test-function"
        self.memory_limit_in_mb = 128
        self.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:test-function"


@pytest.fixture
def authorizer_event():
    return {
        "requestContext": {
            "authorizer": {
                "lambda": {
                    "tenantid": "t-001",
                    "appid": "app-001",
                    "tier": "basic",
                    "sub": "user-1",
                }
            }
        }
    }


@pytest.fixture
def fake_agent():
    agent = MagicMock(spec=AgentRecord)
    agent.agent_name = "test-agent"
    agent.version = "1"
    agent.invocation_mode = InvocationMode.STREAMING
    agent.tier_minimum = "basic"
    return agent


@pytest.fixture
def fake_tenant():
    return TenantContext(tenant_id="t-001", app_id="app-001", tier="basic", sub="user-1")


def test_send_streaming_response():
    mock_stream = MagicMock()
    body = b"hello world"
    headers = {"Content-Type": "text/plain"}

    _send_streaming_response(mock_stream, 200, body, headers)

    assert mock_stream.write.call_count == 2
    preamble = json.loads(mock_stream.write.call_args_list[0][0][0].decode("utf-8").rstrip("\0"))
    assert preamble["statusCode"] == 200
    assert preamble["headers"] == headers
    assert mock_stream.write.call_args_list[1][0][0] == body


@patch("src.bridge.handler.get_agent_record")
@patch("src.bridge.handler.invoke_agent")
def test_handler_wraps_non_streaming_response(mock_invoke, mock_get_agent, authorizer_event):
    mock_stream = MagicMock()
    mock_get_agent.return_value = MagicMock(tier_minimum="basic")

    mock_invoke.return_value = {
        "statusCode": 201,
        "headers": {"X-Test": "Value"},
        "body": json.dumps({"result": "ok"}),
    }

    event = {
        **authorizer_event,
        "httpMethod": "POST",
        "path": "/v1/agents/test-agent/invoke",
        "pathParameters": {"agentName": "test-agent"},
        "body": json.dumps({"input": "hi"}),
    }

    result = handler(event, FakeLambdaContext(), response_stream=mock_stream)

    assert result is None
    assert mock_stream.write.call_count == 2
    preamble = json.loads(mock_stream.write.call_args_list[0][0][0].decode("utf-8").rstrip("\0"))
    assert preamble["statusCode"] == 201
    assert preamble["headers"]["X-Test"] == "Value"
    assert json.loads(mock_stream.write.call_args_list[1][0][0].decode("utf-8")) == {"result": "ok"}


@patch("src.bridge.handler.get_agent_record")
@patch("src.bridge.handler.invoke_agent")
def test_handler_returns_directly_when_no_stream(mock_invoke, mock_get_agent, authorizer_event):
    mock_get_agent.return_value = MagicMock(tier_minimum="basic")
    expected_response = {"statusCode": 200, "body": "ok"}
    mock_invoke.return_value = expected_response

    event = {
        **authorizer_event,
        "httpMethod": "POST",
        "path": "/v1/agents/test-agent/invoke",
        "pathParameters": {"agentName": "test-agent"},
        "body": json.dumps({"input": "hi"}),
    }

    result = handler(event, FakeLambdaContext(), response_stream=None)
    assert result == expected_response


@patch("src.bridge.handler.get_agent_record")
@patch("src.bridge.handler.invoke_agent")
def test_handler_handles_streaming_agent_result_none(mock_invoke, mock_get_agent, authorizer_event):
    mock_stream = MagicMock()
    mock_get_agent.return_value = MagicMock(tier_minimum="basic")
    mock_invoke.return_value = None  # Streaming agent returns None

    event = {
        **authorizer_event,
        "httpMethod": "POST",
        "path": "/v1/agents/test-agent/invoke",
        "pathParameters": {"agentName": "test-agent"},
        "body": json.dumps({"input": "hi"}),
    }

    result = handler(event, FakeLambdaContext(), response_stream=mock_stream)
    assert result is None
    # No extra writes from handler wrapper since result is None
    mock_stream.write.assert_not_called()


# ---------------------------------------------------------------------------
# handle_streaming_invocation unit tests
# ---------------------------------------------------------------------------


@patch("src.bridge.handler.log_invocation")
@patch("src.bridge.handler.requests")
def test_handle_streaming_invocation_streams_lines(
    mock_requests, mock_log, fake_agent, fake_tenant
):
    """Each non-empty SSE line from the runtime is forwarded to the response stream."""
    mock_stream = MagicMock()
    mock_response = MagicMock()
    mock_response.iter_lines.return_value = [b"data: line1", b"data: line2", b""]
    mock_requests.post.return_value.__enter__ = lambda s: mock_response
    mock_requests.post.return_value.__exit__ = MagicMock(return_value=False)

    result = handle_streaming_invocation(
        url="http://runtime:8080",
        headers={"x-tenant-id": "t-001"},
        payload={"input": "hello"},
        agent=fake_agent,
        tenant_context=fake_tenant,
        invocation_id="inv-001",
        start_time=0.0,
        response_stream=mock_stream,
        request_id="req-001",
        session_id="sess-001",
    )

    assert result is None
    # First write is the preamble, subsequent writes are SSE lines
    preamble = json.loads(mock_stream.write.call_args_list[0][0][0].decode("utf-8").rstrip("\0"))
    assert preamble["statusCode"] == 200
    assert preamble["headers"]["Content-Type"] == "text/event-stream"
    # Non-empty lines should be forwarded with SSE double-newline delimiter
    written_lines = [c[0][0] for c in mock_stream.write.call_args_list[1:]]
    assert b"data: line1\n\n" in written_lines
    assert b"data: line2\n\n" in written_lines
    # Empty line must not be written
    for w in written_lines:
        assert w != b"\n\n"


@patch("src.bridge.handler.log_invocation")
@patch("src.bridge.handler.requests")
def test_handle_streaming_invocation_empty_stream(mock_requests, mock_log, fake_agent, fake_tenant):
    """An empty SSE stream (no lines) writes only the preamble and returns None."""
    mock_stream = MagicMock()
    mock_response = MagicMock()
    mock_response.iter_lines.return_value = []
    mock_requests.post.return_value.__enter__ = lambda s: mock_response
    mock_requests.post.return_value.__exit__ = MagicMock(return_value=False)

    result = handle_streaming_invocation(
        url="http://runtime:8080",
        headers={},
        payload={},
        agent=fake_agent,
        tenant_context=fake_tenant,
        invocation_id="inv-002",
        start_time=0.0,
        response_stream=mock_stream,
        request_id="req-002",
        session_id=None,
    )

    assert result is None
    # Only one write: the preamble
    assert mock_stream.write.call_count == 1
    preamble = json.loads(mock_stream.write.call_args_list[0][0][0].decode("utf-8").rstrip("\0"))
    assert preamble["statusCode"] == 200


def test_handle_streaming_invocation_no_response_stream_returns_error(fake_agent, fake_tenant):
    """When response_stream is None, an error response dict is returned immediately."""
    result = handle_streaming_invocation(
        url="http://runtime:8080",
        headers={},
        payload={},
        agent=fake_agent,
        tenant_context=fake_tenant,
        invocation_id="inv-003",
        start_time=0.0,
        response_stream=None,
        request_id="req-003",
        session_id=None,
    )

    assert result is not None
    assert result["statusCode"] == 500
    body = json.loads(result["body"])
    error = body.get("error", body)
    is_internal = "INTERNAL_ERROR" in error.get("code", "")
    is_streaming = "streaming" in error.get("message", "").lower()
    assert is_internal or is_streaming


@patch("src.bridge.handler.log_invocation")
@patch("src.bridge.handler.requests")
def test_handle_streaming_invocation_http_error_propagates(
    mock_requests, mock_log, fake_agent, fake_tenant
):
    """An HTTP error from the runtime (4xx/5xx) propagates as an exception."""

    http_error_cls = type("HTTPError", (Exception,), {})
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = http_error_cls("502 Bad Gateway")
    mock_requests.post.return_value.__enter__ = lambda s: mock_response
    mock_requests.post.return_value.__exit__ = MagicMock(return_value=False)

    with pytest.raises(http_error_cls):
        handle_streaming_invocation(
            url="http://runtime:8080",
            headers={},
            payload={},
            agent=fake_agent,
            tenant_context=fake_tenant,
            invocation_id="inv-004",
            start_time=0.0,
            response_stream=MagicMock(),
            request_id="req-004",
            session_id=None,
        )


@patch("src.bridge.handler.log_invocation")
@patch("src.bridge.handler.requests")
def test_handle_streaming_invocation_uses_mock_session_when_no_session(
    mock_requests, mock_log, fake_agent, fake_tenant
):
    """session_id=None should use 'mock-session-id' fallback for logging."""
    mock_stream = MagicMock()
    mock_response = MagicMock()
    mock_response.iter_lines.return_value = []
    mock_requests.post.return_value.__enter__ = lambda s: mock_response
    mock_requests.post.return_value.__exit__ = MagicMock(return_value=False)

    handle_streaming_invocation(
        url="http://runtime:8080",
        headers={},
        payload={},
        agent=fake_agent,
        tenant_context=fake_tenant,
        invocation_id="inv-005",
        start_time=0.0,
        response_stream=mock_stream,
        request_id="req-005",
        session_id=None,
    )

    mock_log.assert_called_once()
    _, kwargs = mock_log.call_args
    assert kwargs.get("session_id") == "mock-session-id"


@patch("src.bridge.handler.log_invocation")
@patch("src.bridge.handler.requests")
def test_handle_streaming_invocation_logs_after_stream_closes(
    mock_requests, mock_log, fake_agent, fake_tenant
):
    """log_invocation is called exactly once after the stream finishes."""
    mock_stream = MagicMock()
    mock_response = MagicMock()
    mock_response.iter_lines.return_value = [b"data: chunk"]
    mock_requests.post.return_value.__enter__ = lambda s: mock_response
    mock_requests.post.return_value.__exit__ = MagicMock(return_value=False)

    handle_streaming_invocation(
        url="http://runtime:8080",
        headers={},
        payload={},
        agent=fake_agent,
        tenant_context=fake_tenant,
        invocation_id="inv-006",
        start_time=0.0,
        response_stream=mock_stream,
        request_id="req-006",
        session_id="s-006",
    )

    mock_log.assert_called_once()
    _, kwargs = mock_log.call_args
    assert kwargs.get("session_id") == "s-006"
