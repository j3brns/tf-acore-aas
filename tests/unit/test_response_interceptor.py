from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

# Add project root and src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import gateway.interceptors.response_interceptor as response_interceptor

handler = response_interceptor.handler


class FakeLambdaContext:
    function_name = "interceptor-response"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:interceptor-response"
    aws_request_id = "req-123"


@pytest.fixture(scope="module")
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
    os.environ["AWS_REGION"] = "eu-west-2"


@pytest.fixture(scope="module")
def mock_aws_services(aws_credentials):
    with mock_aws():
        yield


@pytest.fixture(scope="module")
def setup_data(mock_aws_services):
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")

    # Create Tools table
    ddb.create_table(
        TableName="platform-tools",
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

    tools_table = ddb.Table("platform-tools")

    # Global tool: basic
    tools_table.put_item(
        Item={
            "PK": "TOOL#calculator",
            "SK": "GLOBAL",
            "tool_name": "calculator",
            "tier_minimum": "basic",
            "enabled": True,
        }
    )

    # Global tool: premium
    tools_table.put_item(
        Item={
            "PK": "TOOL#heavy-compute",
            "SK": "GLOBAL",
            "tool_name": "heavy-compute",
            "tier_minimum": "premium",
            "enabled": True,
        }
    )

    # Tenant-specific tool
    tools_table.put_item(
        Item={
            "PK": "TOOL#custom-tool",
            "SK": "TENANT#t-001",
            "tool_name": "custom-tool",
            "tier_minimum": "standard",
            "enabled": True,
        }
    )

    yield


@pytest.fixture(autouse=True)
def reset_module_cache():
    response_interceptor._ssm_client = None
    response_interceptor._pii_patterns = []
    response_interceptor._pii_cache_expiry = 0
    yield
    response_interceptor._ssm_client = None
    response_interceptor._pii_patterns = []
    response_interceptor._pii_cache_expiry = 0


def seed_pii_patterns() -> None:
    ssm = boto3.client("ssm", region_name="eu-west-2")
    pii_patterns = {
        "email": r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
        "uk_ni": r"[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s*\d{2}\s*\d{2}\s*\d{2}\s*[A-D]",
        "uk_nhs": r"\d{3}\s*\d{3}\s*\d{4}",
        "sort_code": r"\d{2}-\d{2}-\d{2}",
        "account_number": r"\b\d{8}\b",
    }
    ssm.put_parameter(
        Name="/platform/gateway/pii-patterns/default",
        Value=json.dumps(pii_patterns),
        Type="String",
        Overwrite=True,
    )


def test_filter_tools_list_by_payload_tier(setup_data):
    event = {
        "mcp": {
            "gatewayResponse": {
                "body": {
                    "tools": [
                        {"name": "calculator", "tierMinimum": "basic"},
                        {"name": "search", "tierMinimum": "standard"},
                        {"name": "heavy-compute", "tierMinimum": "premium"},
                    ]
                },
                "headers": {"x-tenant-id": "t-001", "x-app-id": "app-001", "x-tier": "basic"},
            }
        }
    }

    response = handler(event, FakeLambdaContext())
    tools = response["mcp"]["transformedGatewayResponse"]["body"]["tools"]
    assert [tool["name"] for tool in tools] == ["calculator"]


def test_filter_tools_list_uses_registry_when_tier_missing(setup_data):
    event = {
        "mcp": {
            "gatewayResponse": {
                "body": {
                    "tools": [
                        {"name": "calculator"},
                        {"name": "heavy-compute"},
                    ]
                },
                "headers": {"x-tenant-id": "t-001", "x-app-id": "app-001", "x-tier": "basic"},
            }
        }
    }

    response = handler(event, FakeLambdaContext())

    tools = response["mcp"]["transformedGatewayResponse"]["body"]["tools"]
    assert [tool["name"] for tool in tools] == ["calculator"]


def test_missing_tenant_context_passthrough(setup_data):
    event = {
        "mcp": {
            "gatewayResponse": {
                "body": {"some": "data"},
                "headers": {},  # Missing headers
            }
        }
    }

    response = handler(event, FakeLambdaContext())

    assert response["mcp"]["transformedGatewayResponse"]["body"] == {"some": "data"}


def test_redact_pii_patterns_in_tool_call(setup_data):
    seed_pii_patterns()

    event = {
        "mcp": {
            "gatewayResponse": {
                "body": {
                    "text": (
                        "Email a.user@example.com, NI AB 12 34 56 A, NHS 943 476 5919, "
                        "sort code 12-34-56, account 12345678"  # pragma: allowlist secret
                    ),
                    "nested": {
                        "secret": "backup@example.org"  # pragma: allowlist secret
                    },
                },
                "headers": {"x-tenant-id": "t-001", "x-app-id": "app-001", "x-tier": "basic"},
            }
        }
    }

    response = handler(event, FakeLambdaContext())

    body = response["mcp"]["transformedGatewayResponse"]["body"]
    assert "a.user@example.com" not in body["text"]
    assert "AB 12 34 56 A" not in body["text"]
    assert "943 476 5919" not in body["text"]
    assert "12-34-56" not in body["text"]
    assert "12345678" not in body["text"]
    assert "[REDACTED]" in body["text"]
    assert body["nested"]["secret"] == "[REDACTED]"


def test_clean_payload_passthrough(setup_data):
    seed_pii_patterns()

    event = {
        "mcp": {
            "gatewayResponse": {
                "body": {"text": "this response is clean", "metadata": {"count": 2}},
                "headers": {"x-tenant-id": "t-001", "x-app-id": "app-001", "x-tier": "basic"},
            }
        }
    }

    response = handler(event, FakeLambdaContext())
    body = response["mcp"]["transformedGatewayResponse"]["body"]
    assert body == {"text": "this response is clean", "metadata": {"count": 2}}


def test_ssm_fetch_failure_fallback_to_default_patterns(setup_data):
    event = {
        "mcp": {
            "gatewayResponse": {
                "body": {
                    "text": (
                        "Contact failover.user@example.com and quote sort code 12-34-56."
                        # pragma: allowlist secret
                    )
                },
                "headers": {"x-tenant-id": "t-001", "x-app-id": "app-001", "x-tier": "basic"},
            }
        }
    }

    with patch("gateway.interceptors.response_interceptor.get_ssm", side_effect=RuntimeError):
        response = handler(event, FakeLambdaContext())

    text = response["mcp"]["transformedGatewayResponse"]["body"]["text"]
    assert "failover.user@example.com" not in text
    assert "12-34-56" not in text
    assert text.count("[REDACTED]") >= 2
