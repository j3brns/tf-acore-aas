from __future__ import annotations
# ruff: noqa: I001

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from botocore.exceptions import ClientError

from data_access.models import InvocationMode

from src.bridge.config_provider import ConfigProvider
from src.bridge.runtime_invoker import RuntimeInvoker


def _agent() -> MagicMock:
    agent = MagicMock()
    agent.agent_name = "echo-agent"
    agent.version = "1.0.0"
    agent.invocation_mode = InvocationMode.SYNC
    return agent


def _tenant_context() -> MagicMock:
    tenant_context = MagicMock()
    tenant_context.tenant_id = "t-123"
    tenant_context.app_id = "app-123"
    return tenant_context


def test_config_provider_caches_fetches_until_refresh() -> None:
    calls = {"count": 0}

    def fetcher() -> dict[str, str]:
        calls["count"] += 1
        return {"runtime_region": "eu-west-1", "mock_runtime_url": None}

    provider = ConfigProvider(
        fetcher=fetcher,
        fallback_factory=lambda: {"runtime_region": "eu-west-1", "mock_runtime_url": None},
        ttl_seconds=60,
    )

    assert provider.get()["runtime_region"] == "eu-west-1"
    assert provider.get()["runtime_region"] == "eu-west-1"
    assert provider.get(force_refresh=True)["runtime_region"] == "eu-west-1"
    assert calls["count"] == 2


def test_config_provider_uses_fallback_when_fetch_fails() -> None:
    provider = ConfigProvider(
        fetcher=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        fallback_factory=lambda: {"runtime_region": "eu-central-1", "mock_runtime_url": None},
        ttl_seconds=60,
    )

    assert provider.get()["runtime_region"] == "eu-central-1"


def test_runtime_invoker_retries_real_runtime_after_failover() -> None:
    agent = _agent()
    tenant_context = _tenant_context()
    service_unavailable = ClientError(
        {
            "Error": {"Code": "ServiceUnavailableException", "Message": "unavailable"},
            "ResponseMetadata": {"HTTPStatusCode": 503},
        },
        "InvokeAgentRuntime",
    )
    success = {"statusCode": 200, "headers": {}, "body": json.dumps({"status": "success"})}

    get_config = MagicMock(
        side_effect=[
            {"runtime_region": "eu-west-1", "mock_runtime_url": None},
            {"runtime_region": "eu-central-1", "mock_runtime_url": None},
        ]
    )
    invoke_real_runtime = MagicMock(side_effect=[service_unavailable, success])
    trigger_failover = MagicMock(return_value="eu-central-1")

    invoker = RuntimeInvoker(
        get_config=get_config,
        invoke_mock_runtime=MagicMock(),
        invoke_real_runtime=invoke_real_runtime,
        is_runtime_unavailable_error=lambda exc: isinstance(exc, ClientError),
        trigger_failover=trigger_failover,
        runtime_failure_response=MagicMock(),
    )

    response = invoker.invoke(
        agent=agent,
        tenant_context=tenant_context,
        prompt="prompt",
        session_id=None,
        webhook_id=None,
        request_id="req-1",
        response_stream=None,
    )

    assert response["statusCode"] == 200
    trigger_failover.assert_called_once_with("eu-west-1")
    assert invoke_real_runtime.call_args_list[0].args[0] == "eu-west-1"
    assert invoke_real_runtime.call_args_list[1].args[0] == "eu-central-1"


def test_runtime_invoker_prefers_mock_runtime_when_configured() -> None:
    agent = _agent()
    tenant_context = _tenant_context()
    invoke_mock_runtime = MagicMock(return_value={"statusCode": 200, "body": "{}"})

    invoker = RuntimeInvoker(
        get_config=MagicMock(
            return_value={"runtime_region": "eu-west-1", "mock_runtime_url": "http://mock"}
        ),
        invoke_mock_runtime=invoke_mock_runtime,
        invoke_real_runtime=MagicMock(),
        is_runtime_unavailable_error=MagicMock(return_value=False),
        trigger_failover=MagicMock(),
        runtime_failure_response=MagicMock(),
    )

    response = invoker.invoke(
        agent=agent,
        tenant_context=tenant_context,
        prompt="prompt",
        session_id=None,
        webhook_id=None,
        request_id="req-1",
        response_stream=None,
    )

    assert response["statusCode"] == 200
    assert invoke_mock_runtime.call_args.args[0] == "http://mock"
