from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from boto3.dynamodb.conditions import Key
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


def test_list_agents_returns_openapi_shape_and_tier_filtered(setup_data):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    table = ddb.Table("platform-agents")
    table.put_item(
        Item={
            "PK": "AGENT#premium-agent",
            "SK": "VERSION#2.0.0",
            "agent_name": "premium-agent",
            "version": "2.0.0",
            "owner_team": "platform-test",
            "tier_minimum": "premium",
            "layer_hash": "1111",
            "layer_s3_key": "k2",
            "script_s3_key": "s2",
            "deployed_at": "2026-01-02T00:00:00Z",
            "invocation_mode": "sync",
            "streaming_enabled": False,
        }
    )

    event = {
        "httpMethod": "GET",
        "path": "/v1/agents",
        "pathParameters": {},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
    }

    response = handler(event, FakeLambdaContext())
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "items" in body
    assert len(body["items"]) == 1
    assert body["items"][0]["agentName"] == "echo-agent"
    assert body["items"][0]["latestVersion"] == "1.0.0"


def test_get_agent_detail_returns_latest_version_and_versions(setup_data):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    table = ddb.Table("platform-agents")
    table.put_item(
        Item={
            "PK": "AGENT#echo-agent",
            "SK": "VERSION#1.1.0",
            "agent_name": "echo-agent",
            "version": "1.1.0",
            "owner_team": "platform-test",
            "tier_minimum": "basic",
            "layer_hash": "2222",
            "layer_s3_key": "k3",
            "script_s3_key": "s3",
            "deployed_at": "2026-01-03T00:00:00Z",
            "invocation_mode": "streaming",
            "streaming_enabled": True,
        }
    )

    event = {
        "httpMethod": "GET",
        "path": "/v1/agents/echo-agent",
        "pathParameters": {"agentName": "echo-agent"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
    }

    response = handler(event, FakeLambdaContext())
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["agentName"] == "echo-agent"
    assert body["latestVersion"] == "1.1.0"
    assert len(body["versions"]) == 2
    assert body["versions"][0]["version"] == "1.1.0"


def test_handler_rejects_legacy_invoke_route(setup_data):
    event = {
        "path": "/v1/invoke",
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps({"agentName": "echo-agent", "input": "Hello"}),
    }

    response = handler(event, FakeLambdaContext())
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["error"]["code"] == "NOT_FOUND"


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
        "path": "/v1/agents/premium-agent/invoke",
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
        "path": "/v1/agents/missing-agent/invoke",
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


def test_get_agent_detail_not_found(setup_data):
    event = {
        "httpMethod": "GET",
        "path": "/v1/agents/missing-agent",
        "pathParameters": {"agentName": "missing-agent"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
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
        "path": "/v1/agents/async-agent/invoke",
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
        job_item = jobs_table.get_item(Key={"PK": "TENANT#t-001", "SK": f"JOB#{body['jobId']}"})
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
        "path": "/v1/agents/stream-agent/invoke",
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


def test_handler_session_id_propagation(setup_data):
    event = {
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
        "body": json.dumps({"input": "Hello", "sessionId": "provided-session-123"}),
    }

    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = [
            b'data: {"type": "text", "content": "Echo"}',
            b"data: [DONE]",
        ]
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        response = handler(event, FakeLambdaContext())

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["sessionId"] == "provided-session-123"

        # Verify it was logged with the provided session ID
        ddb = boto3.resource("dynamodb", region_name="eu-west-2")
        inv_table = ddb.Table("platform-invocations")

        # Find the record - SK starts with INV#
        items = inv_table.query(KeyConditionExpression=Key("PK").eq("TENANT#t-001"))["Items"]
        assert any(item["session_id"] == "provided-session-123" for item in items)


def test_handler_session_id_from_runtime(setup_data):
    event = {
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
        "body": json.dumps({"input": "Hello"}),
    }

    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = [
            b'data: {"type": "session", "sessionId": "runtime-session-456"}',
            b'data: {"type": "text", "content": "Echo"}',
            b"data: [DONE]",
        ]
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        response = handler(event, FakeLambdaContext())

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["sessionId"] == "runtime-session-456"

        # Verify it was logged with the runtime session ID
        ddb = boto3.resource("dynamodb", region_name="eu-west-2")
        inv_table = ddb.Table("platform-invocations")

        items = inv_table.query(KeyConditionExpression=Key("PK").eq("TENANT#t-001"))["Items"]
        assert any(item["session_id"] == "runtime-session-456" for item in items)


def test_get_job_status_returns_job_payload(setup_data):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    jobs_table = ddb.Table("platform-jobs")
    jobs_table.put_item(
        Item={
            "PK": "TENANT#t-001",
            "SK": "JOB#job-123",
            "job_id": "job-123",
            "tenant_id": "t-001",
            "agent_name": "echo-agent",
            "status": "running",
            "created_at": "2026-03-01T10:00:00Z",
            "started_at": "2026-03-01T10:00:05Z",
            "webhook_delivered": False,
        }
    )

    event = {
        "httpMethod": "GET",
        "path": "/v1/jobs/job-123",
        "pathParameters": {"jobId": "job-123"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
    }

    response = handler(event, FakeLambdaContext())
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["jobId"] == "job-123"
    assert body["tenantId"] == "t-001"
    assert body["status"] == "running"
    assert body["resultUrl"] is None


def test_get_job_status_rejects_non_contract_route_with_job_id_path_param(setup_data):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    jobs_table = ddb.Table("platform-jobs")
    jobs_table.put_item(
        Item={
            "PK": "TENANT#t-001",
            "SK": "JOB#job-123",
            "job_id": "job-123",
            "tenant_id": "t-001",
            "agent_name": "echo-agent",
            "status": "running",
            "created_at": "2026-03-01T10:00:00Z",
        }
    )

    event = {
        "httpMethod": "GET",
        "path": "/v1/platform/ops/jobs/job-123",
        "pathParameters": {"jobId": "job-123"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
    }

    response = handler(event, FakeLambdaContext())
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["error"]["code"] == "NOT_FOUND"


def test_get_job_status_accepts_stage_prefixed_contract_route(setup_data):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    jobs_table = ddb.Table("platform-jobs")
    jobs_table.put_item(
        Item={
            "PK": "TENANT#t-001",
            "SK": "JOB#job-123",
            "job_id": "job-123",
            "tenant_id": "t-001",
            "agent_name": "echo-agent",
            "status": "running",
            "created_at": "2026-03-01T10:00:00Z",
        }
    )

    event = {
        "httpMethod": "GET",
        "path": "/prod/v1/jobs/job-123",
        "pathParameters": {"jobId": "job-123"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
    }

    response = handler(event, FakeLambdaContext())
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["jobId"] == "job-123"


def test_get_job_status_generates_presigned_result_url_with_expected_expiry(setup_data):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    jobs_table = ddb.Table("platform-jobs")

    s3 = boto3.client("s3", region_name="eu-west-2")
    s3.create_bucket(
        Bucket="platform-job-results",
        CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
    )
    result_key = "tenants/t-001/results/job-456.json"
    s3.put_object(Bucket="platform-job-results", Key=result_key, Body=b"{}")

    jobs_table.put_item(
        Item={
            "PK": "TENANT#t-001",
            "SK": "JOB#job-456",
            "job_id": "job-456",
            "tenant_id": "t-001",
            "agent_name": "echo-agent",
            "status": "completed",
            "created_at": "2026-03-01T10:00:00Z",
            "completed_at": "2026-03-01T10:00:30Z",
            "result_s3_key": result_key,
            "webhook_delivered": True,
        }
    )

    event = {
        "httpMethod": "GET",
        "path": "/v1/jobs/job-456",
        "pathParameters": {"jobId": "job-456"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
    }

    with (
        patch("src.bridge.handler.JOB_RESULTS_BUCKET", "platform-job-results"),
        patch("src.bridge.handler.JOB_RESULT_URL_EXPIRY_SECONDS", 900),
    ):
        response = handler(event, FakeLambdaContext())

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "completed"
    assert isinstance(body["resultUrl"], str)

    query = parse_qs(urlparse(body["resultUrl"]).query)
    expires = (query.get("X-Amz-Expires") or query.get("Expires") or [None])[0]
    assert expires == "900"


def test_get_job_status_hides_other_tenants_job(setup_data):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    jobs_table = ddb.Table("platform-jobs")
    jobs_table.put_item(
        Item={
            "PK": "TENANT#t-999",
            "SK": "JOB#job-foreign",
            "job_id": "job-foreign",
            "tenant_id": "t-999",
            "agent_name": "echo-agent",
            "status": "pending",
            "created_at": "2026-03-01T10:00:00Z",
        }
    )

    event = {
        "httpMethod": "GET",
        "path": "/v1/jobs/job-foreign",
        "pathParameters": {"jobId": "job-foreign"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
    }

    response = handler(event, FakeLambdaContext())
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["error"]["code"] == "NOT_FOUND"


def test_register_and_delete_webhook(setup_data):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    jobs_table = ddb.Table("platform-jobs")

    register_event = {
        "httpMethod": "POST",
        "path": "/v1/webhooks",
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps(
            {
                "callbackUrl": "https://example.com/webhooks/platform",
                "events": ["job.completed", "job.failed"],
                "description": "Ops endpoint",
            }
        ),
    }

    register_response = handler(register_event, FakeLambdaContext())
    assert register_response["statusCode"] == 201
    register_body = json.loads(register_response["body"])

    webhook_id = register_body["webhookId"]
    assert register_body["callbackUrl"] == "https://example.com/webhooks/platform"
    assert register_body["events"] == ["job.completed", "job.failed"]
    assert register_body["signatureHeader"] == "X-Platform-Signature"

    webhook_record = jobs_table.get_item(Key={"PK": f"WEBHOOK#{webhook_id}", "SK": "METADATA"})
    assert webhook_record["Item"]["tenant_id"] == "t-001"
    assert webhook_record["Item"]["callback_url"] == "https://example.com/webhooks/platform"
    assert "signature_secret" in webhook_record["Item"]
    assert "signature_secret" not in register_body

    delete_event = {
        "httpMethod": "DELETE",
        "path": f"/v1/webhooks/{webhook_id}",
        "pathParameters": {"webhookId": webhook_id},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
    }

    delete_response = handler(delete_event, FakeLambdaContext())
    assert delete_response["statusCode"] == 204
    deleted = jobs_table.get_item(Key={"PK": f"WEBHOOK#{webhook_id}", "SK": "METADATA"})
    assert "Item" not in deleted


def test_handler_async_uses_registered_webhook_callback(setup_data):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    agents_table = ddb.Table("platform-agents")
    jobs_table = ddb.Table("platform-jobs")

    agents_table.put_item(
        Item={
            "PK": "AGENT#async-webhook-agent",
            "SK": "VERSION#1.0.0",
            "agent_name": "async-webhook-agent",
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
    jobs_table.put_item(
        Item={
            "PK": "WEBHOOK#webhook-001",
            "SK": "METADATA",
            "webhook_id": "webhook-001",
            "tenant_id": "t-001",
            "app_id": "app-001",
            "callback_url": "https://example.com/hooks/job",
            "events": ["job.completed"],
            "created_at": "2026-03-01T09:59:00Z",
            "signature_secret": "test-signature-material",  # pragma: allowlist secret
        }
    )

    event = {
        "path": "/v1/agents/async-webhook-agent/invoke",
        "pathParameters": {"agentName": "async-webhook-agent"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps({"input": "hello", "webhookId": "webhook-001"}),
    }

    with patch("requests.post"):
        response = handler(event, FakeLambdaContext())

    assert response["statusCode"] == 202
    response_body = json.loads(response["body"])
    assert response_body["webhookDelivery"] == "registered"

    job_id = response_body["jobId"]
    job = jobs_table.get_item(Key={"PK": "TENANT#t-001", "SK": f"JOB#{job_id}"})["Item"]
    assert job["webhook_url"] == "https://example.com/hooks/job"


def test_handler_async_rejects_unknown_webhook_id(setup_data):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    table = ddb.Table("platform-agents")
    table.put_item(
        Item={
            "PK": "AGENT#async-agent-missing-webhook",
            "SK": "VERSION#1.0.0",
            "agent_name": "async-agent-missing-webhook",
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
        "path": "/v1/agents/async-agent-missing-webhook/invoke",
        "pathParameters": {"agentName": "async-agent-missing-webhook"},
        "requestContext": {
            "authorizer": {
                "tenantid": "t-001",
                "appid": "app-001",
                "tier": "basic",
                "sub": "user-1",
            }
        },
        "body": json.dumps({"input": "hello", "webhookId": "does-not-exist"}),
    }

    with patch("requests.post"):
        response = handler(event, FakeLambdaContext())

    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["error"]["code"] == "NOT_FOUND"
