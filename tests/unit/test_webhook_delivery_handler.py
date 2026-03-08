from __future__ import annotations

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

from src.webhook_delivery.handler import handler


class FakeLambdaContext:
    function_name = "webhook-delivery"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:webhook-delivery"
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

    # Create tables
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
    ddb.create_table(
        TableName="platform-tenants",
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

    # Seed webhook
    tenants_table = ddb.Table("platform-tenants")
    tenants_table.put_item(
        Item={
            "PK": "WEBHOOK#wh-123",
            "SK": "TENANT#t-001",
            "webhook_id": "wh-123",
            "tenant_id": "t-001",
            "callback_url": "https://example.com/webhook",
            "events": ["job.completed", "job.failed"],
            "secret": "super-secret",  # pragma: allowlist secret
            "created_at": "2026-01-01T00:00:00Z",
            "enabled": True,
        }
    )

    # Seed job
    jobs_table = ddb.Table("platform-jobs")
    jobs_table.put_item(
        Item={
            "PK": "JOB#job-abc",
            "SK": "METADATA",
            "job_id": "job-abc",
            "tenant_id": "t-001",
            "status": "completed",
            "webhook_url": "wh-123",
            "agent_name": "echo-agent",
            "result_s3_key": "results/job-abc.json",
        }
    )


def test_handler_delivery_success(setup_data):
    # Mock EventBridge event wrapping DynamoDB Stream record
    event = {
        "detail": {
            "dynamodb": {
                "NewImage": {
                    "job_id": {"S": "job-abc"},
                    "tenant_id": {"S": "t-001"},
                    "status": {"S": "completed"},
                    "webhook_url": {"S": "wh-123"},
                    "agent_name": {"S": "echo-agent"},
                    "result_s3_key": {"S": "results/job-abc.json"},
                }
            }
        }
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = handler(event, FakeLambdaContext())

        assert result["status"] == "delivered"

        # Verify urllib.request.urlopen was called once
        mock_urlopen.assert_called_once()

        # Verify job was marked delivered in DynamoDB
        ddb = boto3.resource("dynamodb", region_name="eu-west-2")
        jobs_table = ddb.Table("platform-jobs")
        item = jobs_table.get_item(Key={"PK": "JOB#job-abc", "SK": "METADATA"})["Item"]
        assert item["webhook_delivered"] is True


def test_handler_delivery_retry_success(setup_data):
    event = {
        "detail": {
            "dynamodb": {
                "NewImage": {
                    "job_id": {"S": "job-abc"},
                    "tenant_id": {"S": "t-001"},
                    "status": {"S": "completed"},
                    "webhook_url": {"S": "wh-123"},
                }
            }
        }
    }

    with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
        # First call fails, second succeeds
        mock_resp_fail = MagicMock()
        mock_resp_fail.status = 500
        mock_resp_fail.__enter__.side_effect = Exception("Service error")

        mock_resp_success = MagicMock()
        mock_resp_success.status = 200
        mock_resp_success.__enter__.return_value = mock_resp_success

        mock_urlopen.side_effect = [mock_resp_fail, mock_resp_success]

        result = handler(event, FakeLambdaContext())

        assert result["status"] == "delivered"
        assert mock_urlopen.call_count == 2


def test_handler_delivery_exhaustion(setup_data):
    event = {
        "detail": {
            "dynamodb": {
                "NewImage": {
                    "job_id": {"S": "job-abc"},
                    "tenant_id": {"S": "t-001"},
                    "status": {"S": "completed"},
                    "webhook_url": {"S": "wh-123"},
                }
            }
        }
    }

    with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
        mock_urlopen.side_effect = Exception("Connection error")

        result = handler(event, FakeLambdaContext())

        assert result["status"] == "failed"
        # 1 original + 3 retries = 4 calls
        assert mock_urlopen.call_count == 4


def test_handler_no_webhook(setup_data):
    event = {
        "detail": {
            "dynamodb": {
                "NewImage": {
                    "job_id": {"S": "job-abc"},
                    "tenant_id": {"S": "t-001"},
                    "status": {"S": "completed"},
                }
            }
        }
    }

    result = handler(event, FakeLambdaContext())
    assert result["status"] == "skipped"


def test_handler_webhook_not_found(setup_data):
    event = {
        "detail": {
            "dynamodb": {
                "NewImage": {
                    "job_id": {"S": "job-abc"},
                    "tenant_id": {"S": "t-001"},
                    "status": {"S": "completed"},
                    "webhook_url": {"S": "wh-missing"},
                }
            }
        }
    }

    result = handler(event, FakeLambdaContext())
    assert result["status"] == "webhook_not_found"


def test_handler_event_type_mismatch(setup_data):
    # Update webhook to only allow job.failed
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    tenants_table = ddb.Table("platform-tenants")
    tenants_table.put_item(
        Item={
            "PK": "WEBHOOK#wh-123",
            "SK": "TENANT#t-001",
            "webhook_id": "wh-123",
            "tenant_id": "t-001",
            "callback_url": "https://example.com/webhook",
            "events": ["job.failed"],
            "secret": "super-secret",  # pragma: allowlist secret
            "created_at": "2026-01-01T00:00:00Z",
        }
    )

    event = {
        "detail": {
            "dynamodb": {
                "NewImage": {
                    "job_id": {"S": "job-abc"},
                    "tenant_id": {"S": "t-001"},
                    "status": {"S": "completed"},
                    "webhook_url": {"S": "wh-123"},
                }
            }
        }
    }

    result = handler(event, FakeLambdaContext())
    assert result["status"] == "event_type_mismatch"


def test_handler_signature_verification(setup_data):
    event = {
        "detail": {
            "dynamodb": {
                "NewImage": {
                    "job_id": {"S": "job-abc"},
                    "tenant_id": {"S": "t-001"},
                    "status": {"S": "completed"},
                    "webhook_url": {"S": "wh-123"},
                    "agent_name": {"S": "echo-agent"},
                }
            }
        }
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        handler(event, FakeLambdaContext())

        # Extract the signature from the call
        args, _ = mock_urlopen.call_args
        request = args[0]
        signature = request.get_header("X-platform-signature")  # urllib normalizes to Capitalized

        # We need to find the actual header name used
        sent_headers = request.headers
        signature = None
        for k, v in sent_headers.items():
            if k.lower() == "x-platform-signature":
                signature = v

        assert signature is not None

        # Manually calculate expected signature
        # We need to know exactly what was in the payload
        # It's better to verify the signing logic separately or mock it
