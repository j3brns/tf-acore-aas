from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
import requests
from boto3.dynamodb.types import TypeSerializer
from moto import mock_aws

# Add project root and data-access-lib to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))
os.environ.setdefault("POWERTOOLS_TRACE_DISABLED", "true")

from src.webhook_delivery import handler as webhook_handler


class FakeLambdaContext:
    function_name = "webhook-delivery"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:webhook-delivery"
    aws_request_id = "req-123"


@pytest.fixture
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
    os.environ["AWS_REGION"] = "eu-west-2"


@pytest.fixture
def setup_delivery_env(aws_credentials):
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="eu-west-2")
        sqs = boto3.client("sqs", region_name="eu-west-2")

        _key_schema = [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ]
        _attr_defs = [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ]
        ddb.create_table(
            TableName="platform-jobs",
            KeySchema=_key_schema,
            AttributeDefinitions=_attr_defs,
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName="platform-tenants",
            KeySchema=_key_schema,
            AttributeDefinitions=_attr_defs,
            BillingMode="PAY_PER_REQUEST",
        )

        retry_queue_url = sqs.create_queue(QueueName="webhook-retry")["QueueUrl"]
        dlq_queue_url = sqs.create_queue(QueueName="webhook-dlq")["QueueUrl"]

        webhook_handler._sqs_client = None
        webhook_handler.JOBS_TABLE = "platform-jobs"
        webhook_handler.TENANTS_TABLE = "platform-tenants"
        webhook_handler.WEBHOOK_RETRY_QUEUE_URL = retry_queue_url
        webhook_handler.WEBHOOK_DLQ_URL = dlq_queue_url
        webhook_handler.WEBHOOK_MAX_RETRY_ATTEMPTS = 3
        webhook_handler.WEBHOOK_HTTP_TIMEOUT_SECONDS = 5

        yield {
            "ddb": ddb,
            "sqs": sqs,
            "retry_queue_url": retry_queue_url,
            "dlq_queue_url": dlq_queue_url,
        }


def _serialize_item(item: dict[str, object]) -> dict[str, dict[str, object]]:
    serializer = TypeSerializer()
    return {key: serializer.serialize(value) for key, value in item.items()}


def _seed_registration(
    tenants_table,
    *,
    tenant_id: str = "t-001",
    webhook_id: str = "webhook-001",
    events: list[str] | None = None,
) -> None:
    tenants_table.put_item(
        Item={
            "PK": f"TENANT#{tenant_id}",
            "SK": f"WEBHOOK#{webhook_id}",
            "webhook_id": webhook_id,
            "tenant_id": tenant_id,
            "app_id": "app-001",
            "callback_url": "https://example.com/webhooks/platform",
            "events": events or ["job.completed"],
            "created_at": "2026-03-14T10:00:00+00:00",
            "signature_secret": "test-signature-material",  # pragma: allowlist secret
            "status": "active",
        }
    )


def _seed_job(table, **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "PK": "TENANT#t-001",
        "SK": "JOB#job-123",
        "job_id": "job-123",
        "tenant_id": "t-001",
        "app_id": "app-001",
        "agent_name": "async-agent",
        "status": "completed",
        "created_at": "2026-03-14T10:00:00+00:00",
        "completed_at": "2026-03-14T10:01:00+00:00",
        "webhook_id": "webhook-001",
        "webhook_url": "https://example.com/webhooks/platform",
        "webhook_delivered": False,
        "webhook_delivery_attempts": 0,
        "ttl": 9999999999,
    }
    item.update(overrides)
    table.put_item(Item=item)
    return item


def test_delivers_signed_webhook_and_marks_job_delivered(setup_delivery_env):
    jobs_table = setup_delivery_env["ddb"].Table("platform-jobs")
    tenants_table = setup_delivery_env["ddb"].Table("platform-tenants")
    _seed_registration(tenants_table)
    job_item = _seed_job(jobs_table)

    response = MagicMock()
    response.raise_for_status.return_value = None

    with patch.object(webhook_handler.requests, "post", return_value=response) as mock_post:
        result = webhook_handler.handler(
            {
                "Records": [
                    {
                        "eventSource": "aws:dynamodb",
                        "dynamodb": {"NewImage": _serialize_item(job_item)},
                    }
                ]
            },
            FakeLambdaContext(),
        )

    assert result == {"status": "ok"}
    assert mock_post.call_count == 1
    payload_bytes = mock_post.call_args.kwargs["data"]
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["X-Platform-Signature"] == webhook_handler._sign_payload(
        payload_bytes, "test-signature-material"
    )

    stored = jobs_table.get_item(Key={"PK": "TENANT#t-001", "SK": "JOB#job-123"})["Item"]
    assert stored["webhook_delivered"] is True
    assert stored["webhook_delivery_status"] == "delivered"
    assert stored["webhook_delivery_attempts"] == 1


def test_queues_retry_after_delivery_failure(setup_delivery_env):
    jobs_table = setup_delivery_env["ddb"].Table("platform-jobs")
    tenants_table = setup_delivery_env["ddb"].Table("platform-tenants")
    _seed_registration(tenants_table)
    job_item = _seed_job(jobs_table)

    with patch.object(
        webhook_handler.requests,
        "post",
        side_effect=requests.RequestException("temporary failure"),
    ):
        webhook_handler.handler(
            {
                "Records": [
                    {
                        "eventSource": "aws:dynamodb",
                        "dynamodb": {"NewImage": _serialize_item(job_item)},
                    }
                ]
            },
            FakeLambdaContext(),
        )

    stored = jobs_table.get_item(Key={"PK": "TENANT#t-001", "SK": "JOB#job-123"})["Item"]
    assert stored["webhook_delivered"] is False
    assert stored["webhook_delivery_status"] == "retrying"
    assert stored["webhook_delivery_attempts"] == 1
    assert "temporary failure" in stored["webhook_delivery_error"]

    attrs = setup_delivery_env["sqs"].get_queue_attributes(
        QueueUrl=setup_delivery_env["retry_queue_url"],
        AttributeNames=["ApproximateNumberOfMessagesDelayed"],
    )["Attributes"]
    assert attrs["ApproximateNumberOfMessagesDelayed"] == "1"


def test_marks_job_failed_and_sends_dlq_after_retries_exhausted(setup_delivery_env):
    jobs_table = setup_delivery_env["ddb"].Table("platform-jobs")
    tenants_table = setup_delivery_env["ddb"].Table("platform-tenants")
    _seed_registration(tenants_table)
    _seed_job(jobs_table)

    with patch.object(
        webhook_handler.requests,
        "post",
        side_effect=requests.RequestException("still failing"),
    ):
        result = webhook_handler.handler(
            {
                "Records": [
                    {
                        "eventSource": "aws:sqs",
                        "messageId": "msg-123",
                        "body": json.dumps(
                            {
                                "tenantId": "t-001",
                                "appId": "app-001",
                                "jobId": "job-123",
                                "attempt": 4,
                            }
                        ),
                    }
                ]
            },
            FakeLambdaContext(),
        )

    assert result == {"status": "ok"}
    stored = jobs_table.get_item(Key={"PK": "TENANT#t-001", "SK": "JOB#job-123"})["Item"]
    assert stored["webhook_delivered"] is False
    assert stored["webhook_delivery_status"] == "failed"
    assert stored["webhook_delivery_attempts"] == 4
    assert "still failing" in stored["webhook_delivery_error"]

    message = setup_delivery_env["sqs"].receive_message(
        QueueUrl=setup_delivery_env["dlq_queue_url"], MaxNumberOfMessages=1
    )["Messages"][0]
    body = json.loads(message["Body"])
    assert body["jobId"] == "job-123"
    assert body["attempt"] == 4
