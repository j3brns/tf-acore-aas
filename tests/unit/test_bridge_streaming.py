import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))


from src.bridge.handler import _send_streaming_response, handler


class FakeLambdaContext:
    def __init__(self):
        self.aws_request_id = "test-request-id"
        self.function_name = "test-function"
        self.memory_limit_in_mb = 128
        self.invoked_function_arn = "arn:aws:lambda:eu-west-2:123456789012:function:test-function"


@pytest.fixture(autouse=True)
def mock_capabilities():
    """Mock TenantCapabilityClient to allow all agents for testing."""
    with patch("src.bridge.handler.get_capability_client") as mock:
        mock_client = MagicMock()
        mock.return_value = mock_client

        # Policy that allows everything by default for tests
        policy = MagicMock()
        policy.is_enabled.return_value = True
        mock_client.fetch_policy.return_value = policy
        yield mock


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
