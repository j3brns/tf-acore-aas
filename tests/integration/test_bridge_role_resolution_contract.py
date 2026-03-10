from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from src.bridge.handler import handler


class FakeLambdaContext:
    function_name = "bridge"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:bridge"
    aws_request_id = "req-integration-role-resolution"


@pytest.fixture
def aws_credentials():
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


def _create_table(ddb: Any, table_name: str) -> None:
    ddb.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _seed_agent(ddb: Any) -> None:
    ddb.Table("platform-agents").put_item(
        Item={
            "PK": "AGENT#echo-agent",
            "SK": "VERSION#1.0.0",
            "agent_name": "echo-agent",
            "version": "1.0.0",
            "owner_team": "platform-test",
            "tier_minimum": "basic",
            "layer_hash": "hash",
            "layer_s3_key": "layer.zip",
            "script_s3_key": "script.zip",
            "deployed_at": "2026-01-01T00:00:00Z",
            "invocation_mode": "sync",
            "streaming_enabled": False,
            "runtime_arn": "arn:aws:bedrock-agentcore:eu-west-1:210987654321:runtime/echo-agent",
        }
    )


def _invoke_event() -> dict[str, Any]:
    return {
        "httpMethod": "POST",
        "path": "/v1/agents/echo-agent/invoke",
        "pathParameters": {"agentName": "echo-agent"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps({"input": "hello"}),
    }


def test_handler_assume_role_uses_tenant_record_arn(mock_aws_services):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    _create_table(ddb, "platform-agents")
    _create_table(ddb, "platform-tenants")
    _seed_agent(ddb)

    ddb.Table("platform-tenants").put_item(
        Item={
            "PK": "TENANT#t-001",
            "SK": "METADATA",
            "tenantId": "t-001",
            "accountId": "123456789012",
            "executionRoleArn": "arn:aws:iam::123456789012:role/custom-record-role",
        }
    )

    with (
        patch(
            "src.bridge.handler.get_config",
            return_value={"runtime_region": "eu-west-1", "mock_runtime_url": None},
        ),
        patch("src.bridge.handler.get_sts") as mock_get_sts,
        patch("src.bridge.handler.get_runtime_client") as mock_get_runtime_client,
    ):
        mock_sts = MagicMock()
        mock_get_sts.return_value = mock_sts
        mock_sts.assume_role.return_value = {"Credentials": {"AccessKeyId": "foo"}}
        runtime_client = MagicMock()
        runtime_client.invoke_agent_runtime.return_value = {
            "contentType": "application/json",
            "runtimeSessionId": "runtime-session-id-123456789012345",
            "response": MagicMock(read=MagicMock(return_value=b'{"echo":"hello"}')),
            "statusCode": 200,
        }
        mock_get_runtime_client.return_value = runtime_client

        response = handler(_invoke_event(), FakeLambdaContext())

    assert response["statusCode"] == 200
    _, kwargs = mock_sts.assume_role.call_args
    assert kwargs["RoleArn"] == "arn:aws:iam::123456789012:role/custom-record-role"


def test_handler_assume_role_uses_ssm_arn_when_tenant_record_missing_field(mock_aws_services):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    ssm = boto3.client("ssm", region_name="eu-west-2")
    _create_table(ddb, "platform-agents")
    _create_table(ddb, "platform-tenants")
    _seed_agent(ddb)

    ddb.Table("platform-tenants").put_item(
        Item={
            "PK": "TENANT#t-001",
            "SK": "METADATA",
            "tenantId": "t-001",
            "accountId": "123456789012",
        }
    )
    ssm.put_parameter(
        Name="/platform/tenants/t-001/execution-role-arn",
        Value="arn:aws:iam::123456789012:role/custom-ssm-role",
        Type="String",
    )

    with (
        patch(
            "src.bridge.handler.get_config",
            return_value={"runtime_region": "eu-west-1", "mock_runtime_url": None},
        ),
        patch("src.bridge.handler.get_sts") as mock_get_sts,
        patch("src.bridge.handler.get_runtime_client") as mock_get_runtime_client,
    ):
        mock_sts = MagicMock()
        mock_get_sts.return_value = mock_sts
        mock_sts.assume_role.return_value = {"Credentials": {"AccessKeyId": "foo"}}
        runtime_client = MagicMock()
        runtime_client.invoke_agent_runtime.return_value = {
            "contentType": "application/json",
            "runtimeSessionId": "runtime-session-id-123456789012345",
            "response": MagicMock(read=MagicMock(return_value=b'{"echo":"hello"}')),
            "statusCode": 200,
        }
        mock_get_runtime_client.return_value = runtime_client

        response = handler(_invoke_event(), FakeLambdaContext())

    assert response["statusCode"] == 200
    _, kwargs = mock_sts.assume_role.call_args
    assert kwargs["RoleArn"] == "arn:aws:iam::123456789012:role/custom-ssm-role"
