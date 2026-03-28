from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import admin_ops_handler, agent_registry_handler


class _Context:
    function_name = "test-fn"
    function_version = "$LATEST"
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:test-fn"
    memory_limit_in_mb = 128
    aws_request_id = "req-123"


def _event(path: str, method: str = "GET") -> dict[str, Any]:
    return {
        "path": path,
        "httpMethod": method,
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "user-123",
                    "tid": "tenant-123",
                    "tenant_id": "tenant-123",
                    "appid": "app-123",
                    "roles": "Platform.Admin",
                }
            }
        },
    }


def _status(response: dict[str, Any]) -> int:
    return int(response["statusCode"])


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(str(response["body"]))


def test_agent_registry_handler_rejects_non_agent_platform_route() -> None:
    response = agent_registry_handler.lambda_handler(_event("/v1/platform/quota"), _Context())

    assert _status(response) == 405
    assert _body(response)["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_admin_ops_handler_rejects_agent_registry_route() -> None:
    response = admin_ops_handler.lambda_handler(_event("/v1/platform/agents"), _Context())

    assert _status(response) == 405
    assert _body(response)["error"]["code"] == "METHOD_NOT_ALLOWED"
