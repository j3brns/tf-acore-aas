from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# Add project root and data-access-lib to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from src.webhook_delivery.handler import lambda_handler


class FakeLambdaContext:
    function_name = "webhook-delivery"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:webhook-delivery"
    aws_request_id = "req-456"


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
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
    sm = boto3.client("secretsmanager", region_name="eu-west-2")

    # Create tables
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

    # Seed tenant with API key secret
    tenant_id = "t-123"
    secret_arn = sm.create_secret(
        Name=f"platform/tenants/{tenant_id}/api-key",
        SecretString=json.dumps(
            {
                "tenantId": tenant_id,
                "appId": "a-123",
                "apiKey": "test-api-key-12345",  # pragma: allowlist secret
            }
        ),
    )["ARN"]
    tenants_table = ddb.Table("platform-tenants")
    tenants_table.put_item(
        Item={
            "PK": f"TENANT#{tenant_id}",
            "SK": "METADATA",
            "tenantId": tenant_id,
            "appId": "a-123",
            "apiKeySecretArn": secret_arn,
            "status": "active",
            "tier": "standard",
        }
    )

    return {"tenant_id": tenant_id, "apiKey": "test-api-key-12345"}


def test_handler_successful_delivery(setup_data):
    tenant_id = setup_data["tenant_id"]
    job_id = "job-789"
    webhook_url = "https://example.com/webhook"

    # Create job record
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    jobs_table = ddb.Table("platform-jobs")
    jobs_table.put_item(
        Item={
            "PK": f"JOB#{job_id}",
            "SK": "METADATA",
            "jobId": job_id,
            "tenantId": tenant_id,
            "agentName": "echo-agent",
            "status": "completed",
            "webhookUrl": webhook_url,
            "webhookDelivered": False,
            "resultS3Key": f"tenants/{tenant_id}/results/{job_id}.json",
        }
    )

    # Mock DynamoDB Stream event
    event = {
        "Records": [
            {
                "eventName": "MODIFY",
                "dynamodb": {
                    "NewImage": {
                        "PK": {"S": f"JOB#{job_id}"},
                        "SK": {"S": "METADATA"},
                        "jobId": {"S": job_id},
                        "tenantId": {"S": tenant_id},
                        "status": {"S": "completed"},
                        "webhookUrl": {"S": webhook_url},
                        "webhookDelivered": {"BOOL": False},
                        "resultS3Key": {"S": f"tenants/{tenant_id}/results/{job_id}.json"},
                    }
                },
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        lambda_handler(event, FakeLambdaContext())

        # Verify update in DynamoDB
        job = jobs_table.get_item(Key={"PK": f"JOB#{job_id}", "SK": "METADATA"})["Item"]
        assert job["webhookDelivered"] is True

        # Verify webhook call
        assert mock_urlopen.called
        args, kwargs = mock_urlopen.call_args
        req = args[0]
        assert req.full_url == webhook_url
        # urllib.request.Request.get_header name is case-sensitive and
        # urllib often capitalizes only the first letter of the header name
        # so "X-Platform-Signature" becomes "X-platform-signature"
        signature_header = None
        for h_name, h_val in req.headers.items():
            if h_name.lower() == "x-platform-signature":
                signature_header = h_val
                break
        assert signature_header is not None
        assert req.get_header("Content-type") == "application/json"

        # Verify payload
        payload = json.loads(req.data.decode())
        assert payload["jobId"] == job_id
        assert payload["status"] == "completed"
        assert payload["tenantId"] == tenant_id


def test_handler_failure_delivery(setup_data):
    tenant_id = setup_data["tenant_id"]
    job_id = "job-failure"
    webhook_url = "https://example.com/webhook"
    error_msg = "Agent timed out"

    # Create job record
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    jobs_table = ddb.Table("platform-jobs")
    jobs_table.put_item(
        Item={
            "PK": f"JOB#{job_id}",
            "SK": "METADATA",
            "jobId": job_id,
            "tenantId": tenant_id,
            "agentName": "echo-agent",
            "status": "failed",
            "webhookUrl": webhook_url,
            "webhookDelivered": False,
            "errorMessage": error_msg,
        }
    )

    # Mock DynamoDB Stream event
    event = {
        "Records": [
            {
                "eventName": "MODIFY",
                "dynamodb": {
                    "NewImage": {
                        "PK": {"S": f"JOB#{job_id}"},
                        "SK": {"S": "METADATA"},
                        "jobId": {"S": job_id},
                        "tenantId": {"S": tenant_id},
                        "status": {"S": "failed"},
                        "webhookUrl": {"S": webhook_url},
                        "webhookDelivered": {"BOOL": False},
                        "errorMessage": {"S": error_msg},
                    }
                },
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        lambda_handler(event, FakeLambdaContext())

        # Verify update in DynamoDB
        job = jobs_table.get_item(Key={"PK": f"JOB#{job_id}", "SK": "METADATA"})["Item"]
        assert job["webhookDelivered"] is True

        # Verify payload
        args, kwargs = mock_urlopen.call_args
        req = args[0]
        payload = json.loads(req.data.decode())
        assert payload["jobId"] == job_id
        assert payload["status"] == "failed"
        assert payload["errorMessage"] == error_msg


def test_handler_ignores_non_terminal_status(setup_data):
    tenant_id = setup_data["tenant_id"]
    job_id = "job-789"
    webhook_url = "https://example.com/webhook"

    event = {
        "Records": [
            {
                "eventName": "MODIFY",
                "dynamodb": {
                    "NewImage": {
                        "PK": {"S": f"JOB#{job_id}"},
                        "SK": {"S": "METADATA"},
                        "jobId": {"S": job_id},
                        "tenantId": {"S": tenant_id},
                        "status": {"S": "running"},
                        "webhookUrl": {"S": webhook_url},
                        "webhookDelivered": {"BOOL": False},
                    }
                },
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        lambda_handler(event, FakeLambdaContext())
        assert not mock_urlopen.called


def test_handler_ignores_already_delivered(setup_data):
    tenant_id = setup_data["tenant_id"]
    job_id = "job-789"
    webhook_url = "https://example.com/webhook"

    event = {
        "Records": [
            {
                "eventName": "MODIFY",
                "dynamodb": {
                    "NewImage": {
                        "PK": {"S": f"JOB#{job_id}"},
                        "SK": {"S": "METADATA"},
                        "jobId": {"S": job_id},
                        "tenantId": {"S": tenant_id},
                        "status": {"S": "completed"},
                        "webhookUrl": {"S": webhook_url},
                        "webhookDelivered": {"BOOL": True},
                    }
                },
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        lambda_handler(event, FakeLambdaContext())
        assert not mock_urlopen.called


def test_handler_retries_on_error(setup_data):
    tenant_id = setup_data["tenant_id"]
    job_id = "job-retry"
    webhook_url = "https://example.com/webhook"

    # Mock DynamoDB Stream event
    event = {
        "Records": [
            {
                "eventName": "MODIFY",
                "dynamodb": {
                    "NewImage": {
                        "PK": {"S": f"JOB#{job_id}"},
                        "SK": {"S": "METADATA"},
                        "jobId": {"S": job_id},
                        "tenantId": {"S": tenant_id},
                        "status": {"S": "completed"},
                        "webhookUrl": {"S": webhook_url},
                        "webhookDelivered": {"BOOL": False},
                    }
                },
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep") as mock_sleep:
        # Fail twice, then succeed
        mock_response_fail = MagicMock()
        mock_response_fail.getcode.return_value = 500
        mock_response_success = MagicMock()
        mock_response_success.getcode.return_value = 200
        mock_response_success.__enter__.return_value = mock_response_success

        mock_urlopen.side_effect = [
            urllib.error.HTTPError(webhook_url, 500, "Internal Error", {}, None),
            Exception("Network error"),
            mock_response_success,
        ]

        lambda_handler(event, FakeLambdaContext())

        assert mock_urlopen.call_count == 3
        assert mock_sleep.call_count == 2

        # Verify job record updated in the end
        ddb = boto3.resource("dynamodb", region_name="eu-west-2")
        jobs_table = ddb.Table("platform-jobs")
        # Need to put job first so update works
        jobs_table.put_item(
            Item={
                "PK": f"JOB#{job_id}",
                "SK": "METADATA",
                "jobId": job_id,
                "webhookDelivered": False,
            }
        )
        # Re-run handler to verify update
        mock_urlopen.side_effect = [mock_response_success]
        lambda_handler(event, FakeLambdaContext())

        job = jobs_table.get_item(Key={"PK": f"JOB#{job_id}", "SK": "METADATA"})["Item"]
        assert job["webhookDelivered"] is True
