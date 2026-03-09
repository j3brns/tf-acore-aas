from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from moto import mock_aws

# Add project root and src to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from src.bridge.handler import assume_tenant_role, invoke_real_runtime


@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
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


def test_assume_tenant_role_uses_correct_suffix(mock_aws_services):
    tenant_id = "t-123"
    account_id = "123456789012"
    with patch("src.bridge.handler.get_sts") as mock_get_sts:
        mock_sts = MagicMock()
        mock_get_sts.return_value = mock_sts
        mock_sts.assume_role.return_value = {"Credentials": {"AccessKeyId": "foo"}}
        assume_tenant_role(tenant_id, account_id)
        expected_role_arn = (
            f"arn:aws:iam::{account_id}:role/platform-tenant-{tenant_id}-execution-role"
        )
        mock_sts.assume_role.assert_called_once()
        args, kwargs = mock_sts.assume_role.call_args
        assert kwargs["RoleArn"] == expected_role_arn


def test_assume_tenant_role_uses_provided_arn(mock_aws_services):
    tenant_id = "t-123"
    account_id = "123456789012"
    provided_arn = "arn:aws:iam::123456789012:role/custom-role"
    with patch("src.bridge.handler.get_sts") as mock_get_sts:
        mock_sts = MagicMock()
        mock_get_sts.return_value = mock_sts
        mock_sts.assume_role.return_value = {"Credentials": {"AccessKeyId": "foo"}}
        assume_tenant_role(tenant_id, account_id, role_arn=provided_arn)
        mock_sts.assume_role.assert_called_once()
        args, kwargs = mock_sts.assume_role.call_args
        assert kwargs["RoleArn"] == provided_arn


@patch("src.bridge.handler.get_tenant_record")
@patch("src.bridge.handler.assume_tenant_role")
def test_invoke_real_runtime_uses_arn_from_record(mock_assume, mock_get_record, mock_aws_services):
    tenant_context = MagicMock()
    tenant_context.tenant_id = "t-123"
    mock_get_record.return_value = {
        "account_id": "123456789012",
        "executionRoleArn": "arn:aws:iam::123456789012:role/record-role",
    }
    agent = MagicMock()
    # We expect it to return 501 eventually, but we want to check assume_tenant_role call
    from src.bridge.handler import invoke_real_runtime

    invoke_real_runtime("eu-west-1", agent, tenant_context, "prompt", None, None, "req-1", None)
    mock_assume.assert_called_once_with(
        "t-123", "123456789012", role_arn="arn:aws:iam::123456789012:role/record-role"
    )


@patch("src.bridge.handler.get_tenant_record")
@patch("src.bridge.handler.assume_tenant_role")
def test_invoke_real_runtime_falls_back_to_constructed_arn(
    mock_assume, mock_get_record, mock_aws_services
):
    tenant_context = MagicMock()
    tenant_context.tenant_id = "t-123"
    mock_get_record.return_value = {
        "account_id": "123456789012"
        # no executionRoleArn
    }
    agent = MagicMock()
    invoke_real_runtime("eu-west-1", agent, tenant_context, "prompt", None, None, "req-1", None)
    # Passing None to role_arn which will trigger fallback in assume_tenant_role
    mock_assume.assert_called_once_with("t-123", "123456789012", role_arn=None)
