from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from data_access.models import AgentRecord, InvocationMode, TenantTier

from src.bridge.handler import handler


class FakeLambdaContext:
    function_name = "bridge"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:bridge"
    aws_request_id = "req-integration"


def _invoke_event(path: str, path_parameters: dict[str, str] | None, body: dict[str, str]) -> dict:
    return {
        "httpMethod": "POST",
        "path": path,
        "pathParameters": path_parameters,
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps(body),
    }


def _agent(agent_name: str) -> AgentRecord:
    return AgentRecord(
        agent_name=agent_name,
        version="1.0.0",
        owner_team="platform-test",
        tier_minimum=TenantTier.BASIC,
        layer_hash="0000",
        layer_s3_key="layer.zip",
        script_s3_key="script.zip",
        deployed_at="2026-01-01T00:00:00Z",
        invocation_mode=InvocationMode.SYNC,
        streaming_enabled=False,
    )


def test_contract_invoke_route_uses_agent_name_path_parameter():
    event = _invoke_event(
        "/v1/agents/echo-agent/invoke",
        {"agentName": "echo-agent"},
        {"agentName": "wrong-agent", "input": "hello"},
    )

    with (
        patch(
            "src.bridge.handler.get_agent_record",
            return_value=_agent("echo-agent"),
        ) as mock_get_agent,
        patch(
            "src.bridge.handler.invoke_agent",
            return_value={
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"status": "success"}),
            },
        ),
    ):
        response = handler(event, FakeLambdaContext())

    assert response["statusCode"] == 200
    mock_get_agent.assert_called_once_with("echo-agent")


def test_legacy_invoke_route_is_not_accepted():
    event = _invoke_event("/v1/invoke", None, {"agentName": "echo-agent", "input": "hello"})

    response = handler(event, FakeLambdaContext())

    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["error"]["code"] == "NOT_FOUND"
