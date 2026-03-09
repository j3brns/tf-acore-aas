from __future__ import annotations

import argparse
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import backfill_tenant_execution_role_arn as script


@pytest.fixture
def aws_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")  # pragma: allowlist secret
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")  # pragma: allowlist secret
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
    monkeypatch.setenv("AWS_REGION", "eu-west-2")


@pytest.fixture
def mock_aws_services(aws_credentials):
    with mock_aws():
        yield


def _create_tenants_table() -> None:
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
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


def test_backfill_apply_sets_execution_role_fields(mock_aws_services):
    _create_tenants_table()
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    ssm = boto3.client("ssm", region_name="eu-west-2")
    table = ddb.Table("platform-tenants")

    table.put_item(
        Item={
            "PK": "TENANT#t-001",
            "SK": "METADATA",
            "tenantId": "t-001",
            "accountId": "123456789012",
        }
    )
    ssm.put_parameter(
        Name="/platform/tenants/t-001/execution-role-arn",
        Value="arn:aws:iam::123456789012:role/tenant-custom-role",
        Type="String",
    )

    rc = script.run(
        argparse.Namespace(
            region="eu-west-2",
            table_name="platform-tenants",
            param_template="/platform/tenants/{tenant_id}/execution-role-arn",
            tenant_id=None,
            apply=True,
        )
    )

    assert rc == 0
    item = table.get_item(Key={"PK": "TENANT#t-001", "SK": "METADATA"})["Item"]
    assert item["executionRoleArn"] == "arn:aws:iam::123456789012:role/tenant-custom-role"
    assert item["execution_role_arn"] == "arn:aws:iam::123456789012:role/tenant-custom-role"


def test_backfill_fails_on_account_mismatch(mock_aws_services):
    _create_tenants_table()
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    table = ddb.Table("platform-tenants")

    table.put_item(
        Item={
            "PK": "TENANT#t-002",
            "SK": "METADATA",
            "tenantId": "t-002",
            "accountId": "123456789012",
            "executionRoleArn": "arn:aws:iam::999999999999:role/wrong-account-role",
        }
    )

    rc = script.run(
        argparse.Namespace(
            region="eu-west-2",
            table_name="platform-tenants",
            param_template="/platform/tenants/{tenant_id}/execution-role-arn",
            tenant_id=None,
            apply=False,
        )
    )

    assert rc == 1
