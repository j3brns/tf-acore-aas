from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# Add project root and data-access-lib to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from src.bridge.handler import handler


class FakeLambdaContext:
    function_name = "bridge"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:bridge"
    aws_request_id = "req-123"


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
    os.environ["AWS_REGION"] = "eu-west-2"


@pytest.fixture
def mock_aws_services(aws_credentials):
    with mock_aws():
        yield


@pytest.fixture
def setup_data(mock_aws_services):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    ssm = boto3.client("ssm", region_name="eu-west-2")

    # Create tables
    ddb.create_table(
        TableName="platform-agents",
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
    ddb.create_table(
        TableName="platform-invocations",
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
    ddb.create_table(
        TableName="platform-jobs",
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

    # Seed agent
    agents_table = ddb.Table("platform-agents")
    agents_table.put_item(
        Item={
            "PK": "AGENT#echo-agent",
            "SK": "VERSION#1.0.0",
            "agent_name": "echo-agent",
            "version": "1.0.0",
            "owner_team": "platform-test",
            "tier_minimum": "basic",
            "layer_hash": "0000",
            "layer_s3_key": "k",
            "script_s3_key": "s",
            "deployed_at": "2026-01-01T00:00:00Z",
            "invocation_mode": "sync",
            "streaming_enabled": True,
        }
    )

    # Seed SSM
    ssm.put_parameter(Name="/platform/config/runtime-region", Value="eu-west-1", Type="String")
    ssm.put_parameter(
        Name="/platform/config/mock-runtime-url", Value="http://localhost:8765", Type="String"
    )


def test_handler_sync_success(setup_data):
    event = {
        "pathParameters": {"agentName": "echo-agent"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps({"input": "Hello"}),
    }

    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = [
            b'data: {"type": "text", "content": "Echo: "}',
            b'data: {"type": "text", "content": "Hello"}',
            b"data: [DONE]",
        ]
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        response = handler(event, FakeLambdaContext())

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["output"] == "Echo: Hello"
        assert body["status"] == "success"
        assert "invocationId" in body


def test_handler_tier_insufficient(setup_data):
    # Seed agent requiring premium
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    table = ddb.Table("platform-agents")
    table.put_item(
        Item={
            "PK": "AGENT#premium-agent",
            "SK": "VERSION#1.0.0",
            "agent_name": "premium-agent",
            "version": "1.0.0",
            "owner_team": "platform-test",
            "tier_minimum": "premium",
            "layer_hash": "0000",
            "layer_s3_key": "k",
            "script_s3_key": "s",
            "deployed_at": "2026-01-01T00:00:00Z",
            "invocation_mode": "sync",
            "streaming_enabled": False,
        }
    )

    event = {
        "pathParameters": {"agentName": "premium-agent"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps({"input": "Hello"}),
    }

    response = handler(event, FakeLambdaContext())
    assert response["statusCode"] == 403
    body = json.loads(response["body"])
    assert body["error"]["code"] == "FORBIDDEN"


def test_handler_agent_not_found(setup_data):
    event = {
        "pathParameters": {"agentName": "missing-agent"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps({"input": "Hello"}),
    }

    response = handler(event, FakeLambdaContext())
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["error"]["code"] == "NOT_FOUND"


def test_handler_async_accepted(setup_data):
    # Seed async agent
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    table = ddb.Table("platform-agents")
    table.put_item(
        Item={
            "PK": "AGENT#async-agent",
            "SK": "VERSION#1.0.0",
            "agent_name": "async-agent",
            "version": "1.0.0",
            "owner_team": "platform-test",
            "tier_minimum": "basic",
            "layer_hash": "0000",
            "layer_s3_key": "k",
            "script_s3_key": "s",
            "deployed_at": "2026-01-01T00:00:00Z",
            "invocation_mode": "async",
            "streaming_enabled": False,
        }
    )

    event = {
        "pathParameters": {"agentName": "async-agent"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps({"input": "Hello"}),
    }

    with patch("requests.post"):
        response = handler(event, FakeLambdaContext())

        assert response["statusCode"] == 202
        body = json.loads(response["body"])
        assert body["status"] == "accepted"
        assert "jobId" in body

        # Verify job was written to DynamoDB
        jobs_table = ddb.Table("platform-jobs")
        job_item = jobs_table.get_item(Key={"PK": f"JOB#{body['jobId']}", "SK": "METADATA"})
        assert "Item" in job_item
        assert job_item["Item"]["status"] == "pending"


def test_handler_streaming(setup_data):
    # Seed streaming agent
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    table = ddb.Table("platform-agents")
    table.put_item(
        Item={
            "PK": "AGENT#stream-agent",
            "SK": "VERSION#1.0.0",
            "agent_name": "stream-agent",
            "version": "1.0.0",
            "owner_team": "platform-test",
            "tier_minimum": "basic",
            "layer_hash": "0000",
            "layer_s3_key": "k",
            "script_s3_key": "s",
            "deployed_at": "2026-01-01T00:00:00Z",
            "invocation_mode": "streaming",
            "streaming_enabled": True,
        }
    )

    event = {
        "pathParameters": {"agentName": "stream-agent"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps({"input": "Hello"}),
    }

    mock_stream = MagicMock()

    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = [
            b'data: {"type": "text", "content": "Chunk 1"}',
            b"data: [DONE]",
        ]
        mock_post.return_value.__enter__.return_value = mock_response

        response = handler(event, FakeLambdaContext(), response_stream=mock_stream)

        assert response is None
        mock_stream.write.assert_called()
