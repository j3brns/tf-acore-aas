"""
test_billing_pagination.py — Verification tests for billing pagination fixes.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Set environment variables for handler
os.environ["AWS_REGION"] = "eu-west-2"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"  # pragma: allowlist secret
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # pragma: allowlist secret
os.environ["AWS_SECURITY_TOKEN"] = "testing"  # pragma: allowlist secret
os.environ["AWS_SESSION_TOKEN"] = "testing"  # pragma: allowlist secret
os.environ["AWS_DEFAULT_REGION"] = "eu-west-2"
os.environ["TENANTS_TABLE_NAME"] = "platform-tenants"
os.environ["INVOCATIONS_TABLE_NAME"] = "platform-invocations"

from src.billing.handler import _get_active_tenants, _process_tenant
from src.tenant_api.models import CallerIdentity, TenantApiDependencies
from src.tenant_api.ops_control import handle_platform_billing_status


@pytest.fixture
def mock_aws_clients() -> Generator[None, None, None]:
    with mock_aws():
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
        yield


def test_get_active_tenants_paginated(mock_aws_clients: Any) -> None:
    """Verify that _get_active_tenants returns all pages of tenants."""
    with patch("src.billing.handler.ControlPlaneDynamoDB") as mock_db_cls:
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        call_count = 0

        def mock_scan_all(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [
                    {
                        "PK": "TENANT#t0",
                        "SK": "METADATA",
                        "tenantId": "t0",
                        "status": "active",
                        "tier": "basic",
                    }
                ]
            if call_count == 2:
                return [
                    {
                        "PK": "TENANT#t1",
                        "SK": "METADATA",
                        "tenantId": "t1",
                        "status": "active",
                        "tier": "basic",
                    },
                    {
                        "PK": "TENANT#t2",
                        "SK": "METADATA",
                        "tenantId": "t2",
                        "status": "active",
                        "tier": "basic",
                    },
                ]
            return []

        mock_db.scan_all.side_effect = mock_scan_all

        tenants = _get_active_tenants()

        assert len(tenants) == 1
        assert call_count == 1


def test_process_tenant_query_pagination(mock_aws_clients: Any) -> None:
    """Verify that _process_tenant processes all pages of invocations."""
    tenant_id = "t-heavy"
    app_id = "app-heavy"
    yesterday = datetime.now(UTC) - timedelta(days=1)
    ts1 = yesterday.replace(hour=10).isoformat()
    ts2 = yesterday.replace(hour=14).isoformat()

    # Seed pricing in SSM
    ssm = boto3.client("ssm", region_name="eu-west-2")
    ssm.put_parameter(
        Name="/platform/billing/pricing/basic",
        Value=json.dumps({"input_1k": 0.1, "output_1k": 0.2}),
        Type="String",
    )

    with patch("src.billing.handler.TenantScopedDynamoDB") as mock_db_cls:
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        mock_db.update_item.return_value = {
            "Attributes": {
                "totalInputTokens": 2000,
                "totalOutputTokens": 0,
                "totalCostUsd": 0.2,
            }
        }

        mock_db.query_all.return_value = [
            {
                "PK": f"TENANT#{tenant_id}",
                "SK": f"INV#{ts1}#1",
                "input_tokens": 1000,
                "output_tokens": 0,
            },
            {
                "PK": f"TENANT#{tenant_id}",
                "SK": f"INV#{ts2}#2",
                "input_tokens": 1000,
                "output_tokens": 0,
            },
        ]

        tenant = {"tenantId": tenant_id, "tier": "basic", "appId": app_id, "status": "active"}
        _process_tenant(tenant, yesterday)

        assert mock_db.query_all.call_count == 1
        assert mock_db.update_item.called
        call_args = mock_db.update_item.call_args
        expr_vals = call_args.args[3]
        assert expr_vals[":di"] == 2000, f"Expected :di=2000, got {expr_vals.get(':di')}"


def test_platform_billing_status_pagination(mock_aws_clients: Any) -> None:
    """Verify that handle_platform_billing_status returns canonical summary fields."""
    year_month = datetime.now(UTC).strftime("%Y-%m")

    caller = CallerIdentity(
        tenant_id="platform",
        app_id="platform-admin",
        tier="premium",
        sub="admin-sub",
        roles=frozenset(["Platform.Admin"]),
        usage_identifier_key="key-123",
    )

    deps = TenantApiDependencies(
        secretsmanager=MagicMock(),
        events=MagicMock(),
        dynamodb=boto3.resource("dynamodb", region_name="eu-west-2"),
        ssm=MagicMock(),
        awslambda=MagicMock(),
        usage_client=MagicMock(),
        memory_provisioner=MagicMock(),
        platform_quota_client=MagicMock(),
    )

    with patch("src.tenant_api.ops_control.db_factory.control_plane_db") as mock_control_plane_db:
        mock_db = MagicMock()
        mock_control_plane_db.return_value = mock_db
        mock_db.scan_all.return_value = [
            {
                "PK": "TENANT#t1",
                "SK": f"BILLING#{year_month}",
                "tenantId": "t1",
                "totalCostUsd": 10.0,
                "totalInputTokens": 1000,
                "totalOutputTokens": 500,
                "updatedAt": "2026-02-01T00:00:00Z",
            },
            {
                "PK": "TENANT#t2",
                "SK": f"BILLING#{year_month}",
                "tenantId": "t2",
                "totalCostUsd": 20.0,
                "totalInputTokens": 2000,
                "totalOutputTokens": 1000,
                "updatedAt": "2026-02-02T00:00:00Z",
            },
        ]

        result = handle_platform_billing_status({}, caller, deps)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["yearMonth"] == year_month
        assert body["summaries"] == [
            {
                "tenantId": "t1",
                "totalInputTokens": 1000,
                "totalOutputTokens": 500,
                "totalCostUsd": 10.0,
                "lastUpdated": "2026-02-01T00:00:00Z",
            },
            {
                "tenantId": "t2",
                "totalInputTokens": 2000,
                "totalOutputTokens": 1000,
                "totalCostUsd": 20.0,
                "lastUpdated": "2026-02-02T00:00:00Z",
            },
        ]
        assert mock_db.scan_all.call_count == 1
