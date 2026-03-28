from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest
from data_access import TenantContext, TenantScopedDynamoDB, TenantScopedS3
from data_access.models import TenantTier

TENANT_ID = "t-abc123"
OTHER_TENANT_ID = "t-xyz789"
APP_ID = "app-001"
REGION = "eu-west-2"
TABLE_NAME = "platform-invocations"
BUCKET = "platform-results"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


@pytest.fixture
def ctx() -> TenantContext:
    return TenantContext(
        tenant_id=TENANT_ID,
        app_id=APP_ID,
        tier=TenantTier.STANDARD,
        sub="user-001",
    )


@pytest.fixture
def mock_cw() -> MagicMock:
    return MagicMock()


def make_dynamo_db(ctx: TenantContext, *, cw: Any = None) -> tuple[TenantScopedDynamoDB, Any]:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    dynamodb.create_table(
        TableName=TABLE_NAME,
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
    cw_client = cw or MagicMock()
    db = TenantScopedDynamoDB(ctx, dynamodb_resource=dynamodb, cloudwatch_client=cw_client)
    return db, dynamodb


def make_s3(ctx: TenantContext, *, cw: Any = None) -> tuple[TenantScopedS3, Any]:
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket=BUCKET,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    cw_client = cw or MagicMock()
    scoped = TenantScopedS3(ctx, s3_client=s3, cloudwatch_client=cw_client)
    return scoped, s3
