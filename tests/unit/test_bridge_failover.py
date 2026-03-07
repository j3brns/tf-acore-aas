from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
import requests
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
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
    os.environ["AWS_REGION"] = "eu-west-2"
    os.environ["OPS_LOCKS_TABLE"] = "platform-ops-locks"


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
        TableName="platform-ops-locks",
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
            "streaming_enabled": False,
        }
    )

    # Seed SSM
    ssm.put_parameter(Name="/platform/config/runtime-region", Value="eu-west-1", Type="String")
    ssm.put_parameter(
        Name="/platform/config/mock-runtime-url", Value="http://localhost:8765", Type="String"
    )


def test_handler_failover_on_503(setup_data):
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
        # First call fails with 503
        mock_response_503 = MagicMock()
        mock_response_503.status_code = 503
        mock_response_503.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_503
        )

        # Second call (after failover) succeeds
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.iter_lines.return_value = [
            b'data: {"type": "text", "content": "Success after failover"}',
            b"data: [DONE]",
        ]

        mock_post.side_effect = [mock_response_503, mock_response_success]

        response = handler(event, FakeLambdaContext())

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["output"] == "Success after failover"

        # Verify SSM was updated to eu-central-1
        ssm = boto3.client("ssm", region_name="eu-west-2")
        param = ssm.get_parameter(Name="/platform/config/runtime-region")
        assert param["Parameter"]["Value"] == "eu-central-1"


def test_handler_failover_already_in_progress(setup_data):
    # Seed the lock to simulate another instance failing over
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    lock_table = ddb.Table("platform-ops-locks")
    lock_table.put_item(
        Item={
            "PK": "LOCK#runtime-region-failover",
            "SK": "METADATA",
            "lock_id": "other-id",
            "ttl": int(time.time()) + 300,
        }
    )

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
        mock_response_503 = MagicMock()
        mock_response_503.status_code = 503
        mock_response_503.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_503
        )

        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.iter_lines.return_value = [
            b'data: {"type": "text", "content": "Success after wait"}',
            b"data: [DONE]",
        ]

        mock_post.side_effect = [mock_response_503, mock_response_success]

        # In this case, our instance will see the lock, wait, and retry.
        # But for the retry to work, the SSM parameter must be updated by "someone else"
        # Since we are mocking everything, we'll manually update SSM while the lock is held.
        ssm = boto3.client("ssm", region_name="eu-west-2")
        ssm.put_parameter(
            Name="/platform/config/runtime-region",
            Value="eu-central-1",
            Type="String",
            Overwrite=True,
        )

        response = handler(event, FakeLambdaContext())

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["output"] == "Success after wait"
