from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

from src.async_runner import handler as async_runner_handler


class FakeContext:
    function_name = "async-runner"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:async-runner"
    aws_request_id = "req-123"


@pytest.fixture
def aws_credentials() -> None:
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
    os.environ["AWS_REGION"] = "eu-west-2"
    os.environ["JOBS_TABLE"] = "platform-jobs"
    os.environ["RUNTIME_PING_URL"] = "http://runtime.local"


@pytest.fixture
def mock_aws_services(aws_credentials: None) -> None:
    with mock_aws():
        yield


@pytest.fixture
def jobs_table(mock_aws_services: None):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
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

    table = ddb.Table("platform-jobs")
    table.put_item(
        Item={
            "PK": "JOB#job-001",
            "SK": "METADATA",
            "job_id": "job-001",
            "tenant_id": "t-001",
            "agent_name": "echo-agent",
            "status": "pending",
            "created_at": "2026-03-01T00:00:00+00:00",
            "ttl": 1770000000,
        }
    )
    return table


def _event() -> dict[str, str]:
    return {
        "jobId": "job-001",
        "tenantId": "t-001",
        "appId": "app-001",
        "agentName": "echo-agent",
        "sessionId": "sess-001",
        "runtimePingUrl": "http://runtime.local",
    }


def _body(response: dict[str, object]) -> dict[str, object]:
    return json.loads(str(response["body"]))


def test_handler_marks_job_running_on_healthybusy(jobs_table) -> None:
    with patch.object(
        async_runner_handler,
        "_http_get_json",
        return_value={"status": "HealthyBusy"},
    ):
        response = async_runner_handler.handler(_event(), FakeContext())

    assert response["statusCode"] == 200
    payload = _body(response)
    assert payload["status"] == "running"
    assert payload["runtimeStatus"] == "HealthyBusy"

    item = jobs_table.get_item(Key={"PK": "JOB#job-001", "SK": "METADATA"})["Item"]
    assert item["status"] == "running"
    assert "started_at" in item


def test_handler_marks_job_completed_on_healthy(jobs_table) -> None:
    jobs_table.update_item(
        Key={"PK": "JOB#job-001", "SK": "METADATA"},
        UpdateExpression="SET #status = :status, started_at = :started",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": "running",
            ":started": "2026-03-01T00:01:00+00:00",
        },
    )

    with patch.object(async_runner_handler, "_http_get_json", return_value={"status": "Healthy"}):
        response = async_runner_handler.handler(_event(), FakeContext())

    assert response["statusCode"] == 200
    payload = _body(response)
    assert payload["status"] == "completed"
    assert payload["runtimeStatus"] == "Healthy"

    item = jobs_table.get_item(Key={"PK": "JOB#job-001", "SK": "METADATA"})["Item"]
    assert item["status"] == "completed"
    assert "completed_at" in item


def test_handler_rejects_missing_required_fields(jobs_table) -> None:
    del jobs_table
    response = async_runner_handler.handler({"jobId": "job-001"}, FakeContext())
    assert response["statusCode"] == 400
    assert _body(response)["error"]["code"] == "INVALID_REQUEST"
