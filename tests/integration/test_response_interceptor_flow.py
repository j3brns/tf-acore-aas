from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

import gateway.interceptors.response_interceptor as response_interceptor

handler = response_interceptor.handler

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class FakeLambdaContext:
    function_name = "interceptor-response"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:interceptor-response"
    aws_request_id = "req-integration"


@pytest.fixture
def aws_credentials():
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # pragma: allowlist secret
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
    os.environ["AWS_REGION"] = "eu-west-2"


@pytest.fixture
def integration_environment(aws_credentials):
    with mock_aws():
        ssm = boto3.client("ssm", region_name="eu-west-2")
        ssm.put_parameter(
            Name="/platform/gateway/pii-patterns/default",
            Value=json.dumps(
                {
                    "email": r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
                    "sort_code": r"\d{2}-\d{2}-\d{2}",
                    "account_number": r"\b\d{8}\b",
                }
            ),
            Type="String",
        )
        yield


@pytest.fixture(autouse=True)
def reset_response_cache():
    response_interceptor._ssm_client = None
    response_interceptor._pii_patterns = []
    response_interceptor._pii_cache_expiry = 0
    yield
    response_interceptor._ssm_client = None
    response_interceptor._pii_patterns = []
    response_interceptor._pii_cache_expiry = 0


def test_response_interceptor_fixture_flow(integration_environment):
    event = json.loads((FIXTURES_DIR / "response_interceptor_tools_call_event.json").read_text())
    expected_body = json.loads(
        (FIXTURES_DIR / "response_interceptor_tools_call_expected.json").read_text()
    )

    response = handler(event, FakeLambdaContext())
    assert response["interceptorOutputVersion"] == "1.0"
    assert response["mcp"]["transformedGatewayResponse"]["body"] == expected_body
