from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

# Add project root and data-access-lib to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from data_access.models import InvocationMode

from src.bridge.handler import assume_tenant_role, invoke_agent, invoke_real_runtime


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
    os.environ["AWS_REGION"] = "eu-west-2"
    os.environ["MOCK_RUNTIME"] = "false"


@pytest.fixture
def mock_aws_services(aws_credentials):
    with mock_aws():
        yield


def _error_message(response: dict[str, str]) -> str:
    body = json.loads(response["body"])
    return str(body["error"]["message"])


def _agent(mode: InvocationMode = InvocationMode.SYNC) -> MagicMock:
    agent = MagicMock()
    agent.agent_name = "echo-agent"
    agent.version = "1.0.0"
    agent.invocation_mode = mode
    agent.runtime_arn = "arn:aws:bedrock-agentcore:eu-west-1:210987654321:runtime/echo-agent"
    return agent


def _tenant_context() -> MagicMock:
    tenant_context = MagicMock()
    tenant_context.tenant_id = "t-123"
    tenant_context.app_id = "app-123"
    tenant_context.sub = "user-123"
    return tenant_context


def _runtime_response(
    payload: bytes,
    *,
    content_type: str = "application/json",
    runtime_session_id: str = "runtime-session-id-123456789012345",
) -> dict[str, object]:
    return {
        "contentType": content_type,
        "runtimeSessionId": runtime_session_id,
        "response": io.BytesIO(payload),
        "statusCode": 200,
    }


def test_assume_tenant_role_uses_provided_arn(mock_aws_services):
    tenant_id = "t-123"
    provided_arn = "arn:aws:iam::123456789012:role/custom-role"
    with patch("src.bridge.handler.get_sts") as mock_get_sts:
        mock_sts = MagicMock()
        mock_get_sts.return_value = mock_sts
        mock_sts.assume_role.return_value = {"Credentials": {"AccessKeyId": "foo"}}
        assume_tenant_role(tenant_id, provided_arn)
        mock_sts.assume_role.assert_called_once()
        _, kwargs = mock_sts.assume_role.call_args
        assert kwargs["RoleArn"] == provided_arn


@patch("src.bridge.handler.get_runtime_client")
@patch("src.bridge.handler.get_tenant_record")
@patch("src.bridge.handler.assume_tenant_role")
def test_invoke_real_runtime_uses_arn_from_record(
    mock_assume, mock_get_record, mock_get_runtime_client, mock_aws_services
):
    tenant_context = _tenant_context()
    agent = _agent()
    runtime_client = MagicMock()
    runtime_client.invoke_agent_runtime.return_value = _runtime_response(b'{"echo":"Echo: prompt"}')
    mock_get_runtime_client.return_value = runtime_client
    mock_assume.return_value = {
        "AccessKeyId": "assumed-akid",
        "SecretAccessKey": "assumed-secret",  # pragma: allowlist secret
        "SessionToken": "assumed-token",
    }
    mock_get_record.return_value = {
        "account_id": "123456789012",
        "executionRoleArn": "arn:aws:iam::123456789012:role/record-role",
    }

    response = invoke_real_runtime(
        "eu-west-1",
        agent,
        tenant_context,
        "prompt",
        None,
        None,
        "req-1",
        None,
        "inv-1",
        0.0,
    )

    assert response["statusCode"] == 200
    mock_assume.assert_called_once_with("t-123", "arn:aws:iam::123456789012:role/record-role")
    mock_get_runtime_client.assert_called_once_with(
        "eu-west-1",
        credentials={
            "AccessKeyId": "assumed-akid",
            "SecretAccessKey": "assumed-secret",  # pragma: allowlist secret
            "SessionToken": "assumed-token",
        },
    )
    _, kwargs = runtime_client.invoke_agent_runtime.call_args
    assert kwargs["agentRuntimeArn"] == (
        "arn:aws:bedrock-agentcore:eu-west-1:210987654321:runtime/echo-agent"
    )
    assert kwargs["payload"] == json.dumps(
        {
            "prompt": "prompt",
            "input": "prompt",
            "mode": "sync",
            "appid": "app-123",
            "tenantId": "t-123",
            "agentName": "echo-agent",
            "agentVersion": "1.0.0",
        }
    ).encode("utf-8")


@patch("src.bridge.handler.get_runtime_client")
@patch("src.bridge.handler.get_tenant_record")
@patch("src.bridge.handler._get_execution_role_arn_from_ssm")
@patch("src.bridge.handler.assume_tenant_role")
def test_invoke_real_runtime_uses_ssm_arn_when_record_missing(
    mock_assume,
    mock_get_role_from_ssm,
    mock_get_record,
    mock_get_runtime_client,
    mock_aws_services,
):
    tenant_context = _tenant_context()
    agent = _agent()
    runtime_client = MagicMock()
    runtime_client.invoke_agent_runtime.return_value = _runtime_response(b'{"echo":"Echo: prompt"}')
    mock_get_runtime_client.return_value = runtime_client
    mock_assume.return_value = {
        "AccessKeyId": "assumed-akid",
        "SecretAccessKey": "assumed-secret",  # pragma: allowlist secret
        "SessionToken": "assumed-token",
    }
    mock_get_record.return_value = {"account_id": "123456789012"}
    mock_get_role_from_ssm.return_value = "arn:aws:iam::123456789012:role/ssm-role"

    response = invoke_real_runtime(
        "eu-west-1",
        agent,
        tenant_context,
        "prompt",
        None,
        None,
        "req-1",
        None,
        "inv-1",
        0.0,
    )

    assert response["statusCode"] == 200
    mock_assume.assert_called_once_with("t-123", "arn:aws:iam::123456789012:role/ssm-role")
    mock_get_role_from_ssm.assert_called_once_with("t-123")


@patch("src.bridge.handler.get_tenant_record")
@patch("src.bridge.handler._get_execution_role_arn_from_ssm")
@patch("src.bridge.handler.assume_tenant_role")
def test_invoke_real_runtime_errors_when_execution_role_arn_missing(
    mock_assume, mock_get_role_from_ssm, mock_get_record, mock_aws_services
):
    tenant_context = _tenant_context()
    mock_get_record.return_value = {"account_id": "123456789012"}
    mock_get_role_from_ssm.return_value = None
    agent = _agent()

    response = invoke_real_runtime(
        "eu-west-1",
        agent,
        tenant_context,
        "prompt",
        None,
        None,
        "req-1",
        None,
        "inv-1",
        0.0,
    )

    assert response["statusCode"] == 500
    assert _error_message(response) == "Tenant execution role ARN not configured"
    mock_assume.assert_not_called()


@patch("src.bridge.handler.get_tenant_record")
@patch("src.bridge.handler._get_execution_role_arn_from_ssm")
@patch("src.bridge.handler.assume_tenant_role")
def test_invoke_real_runtime_errors_when_execution_role_arn_malformed(
    mock_assume, mock_get_role_from_ssm, mock_get_record, mock_aws_services
):
    tenant_context = _tenant_context()
    mock_get_record.return_value = {"account_id": "123456789012"}
    mock_get_role_from_ssm.return_value = "not-an-arn"
    agent = _agent()

    response = invoke_real_runtime(
        "eu-west-1",
        agent,
        tenant_context,
        "prompt",
        None,
        None,
        "req-1",
        None,
        "inv-1",
        0.0,
    )

    assert response["statusCode"] == 500
    assert _error_message(response) == "Tenant execution role ARN is malformed"
    mock_assume.assert_not_called()


@patch("src.bridge.handler.get_tenant_record")
@patch("src.bridge.handler.assume_tenant_role")
def test_invoke_real_runtime_errors_when_execution_role_arn_account_mismatch(
    mock_assume, mock_get_record, mock_aws_services
):
    tenant_context = _tenant_context()
    mock_get_record.return_value = {
        "account_id": "123456789012",
        "executionRoleArn": "arn:aws:iam::999999999999:role/record-role",
    }
    agent = _agent()

    response = invoke_real_runtime(
        "eu-west-1",
        agent,
        tenant_context,
        "prompt",
        None,
        None,
        "req-1",
        None,
        "inv-1",
        0.0,
    )

    assert response["statusCode"] == 500
    assert _error_message(response) == "Tenant execution role ARN account mismatch"
    mock_assume.assert_not_called()


@patch("src.bridge.handler.get_runtime_client")
@patch("src.bridge.handler.get_tenant_record")
@patch("src.bridge.handler.assume_tenant_role")
def test_invoke_real_runtime_rewrites_runtime_arn_to_active_region(
    mock_assume, mock_get_record, mock_get_runtime_client, mock_aws_services
):
    tenant_context = _tenant_context()
    agent = _agent()
    runtime_client = MagicMock()
    runtime_client.invoke_agent_runtime.return_value = _runtime_response(b'{"echo":"Echo: prompt"}')
    mock_get_runtime_client.return_value = runtime_client
    mock_assume.return_value = {
        "AccessKeyId": "assumed-akid",
        "SecretAccessKey": "assumed-secret",  # pragma: allowlist secret
        "SessionToken": "assumed-token",
    }
    mock_get_record.return_value = {
        "account_id": "123456789012",
        "executionRoleArn": "arn:aws:iam::123456789012:role/record-role",
    }

    response = invoke_real_runtime(
        "eu-central-1",
        agent,
        tenant_context,
        "prompt",
        None,
        None,
        "req-1",
        None,
        "inv-1",
        0.0,
    )

    assert response["statusCode"] == 200
    _, kwargs = runtime_client.invoke_agent_runtime.call_args
    assert kwargs["agentRuntimeArn"] == (
        "arn:aws:bedrock-agentcore:eu-central-1:210987654321:runtime/echo-agent"
    )


def test_invoke_agent_maps_runtime_throttling_to_platform_error():
    tenant_context = _tenant_context()
    agent = _agent()
    throttled = ClientError(
        {
            "Error": {"Code": "ThrottlingException", "Message": "slow down"},
            "ResponseMetadata": {"HTTPStatusCode": 429},
        },
        "InvokeAgentRuntime",
    )

    with (
        patch(
            "src.bridge.handler.get_config",
            return_value={"runtime_region": "eu-west-1", "mock_runtime_url": None},
        ),
        patch("src.bridge.handler.invoke_real_runtime", side_effect=throttled),
        patch("src.bridge.handler.log_invocation") as mock_log_invocation,
    ):
        response = invoke_agent(agent, tenant_context, "prompt", None, None, "req-1", None)

    assert response["statusCode"] == 429
    body = json.loads(response["body"])
    assert body["error"]["code"] == "THROTTLED"
    assert response["headers"]["Retry-After"] == "1"
    _, kwargs = mock_log_invocation.call_args
    assert kwargs["error_code"] == "THROTTLED"


def test_invoke_agent_retries_real_runtime_after_failover():
    tenant_context = _tenant_context()
    agent = _agent()
    service_unavailable = ClientError(
        {
            "Error": {"Code": "ServiceUnavailableException", "Message": "unavailable"},
            "ResponseMetadata": {"HTTPStatusCode": 503},
        },
        "InvokeAgentRuntime",
    )
    success_response = {"statusCode": 200, "headers": {}, "body": json.dumps({"status": "success"})}

    with (
        patch(
            "src.bridge.handler.get_config",
            return_value={"runtime_region": "eu-west-1", "mock_runtime_url": None},
        ),
        patch("src.bridge.handler.trigger_failover", return_value="eu-central-1") as mock_failover,
        patch(
            "src.bridge.handler.invoke_real_runtime",
            side_effect=[service_unavailable, success_response],
        ) as mock_invoke_real_runtime,
    ):
        response = invoke_agent(agent, tenant_context, "prompt", None, None, "req-1", None)

    assert response["statusCode"] == 200
    mock_failover.assert_called_once_with("eu-west-1")
    assert mock_invoke_real_runtime.call_args_list[0].args[0] == "eu-west-1"
    assert mock_invoke_real_runtime.call_args_list[1].args[0] == "eu-central-1"
