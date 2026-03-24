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
from src.tenant_api.handler import (
    CallerIdentity,
    TenantApiDependencies,
    _handle_platform_billing_status,
)


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
    # We patch the _dynamodb resource in the handler
    with patch("src.billing.handler._dynamodb") as mock_ddb_resource:
        mock_table = MagicMock()
        mock_ddb_resource.Table.return_value = mock_table

        call_count = 0

        def mock_scan(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Items": [
                        {
                            "PK": "TENANT#t0",
                            "SK": "METADATA",
                            "tenant_id": "t0",
                            "status": "active",
                            "tier": "basic",
                        }
                    ],
                    "LastEvaluatedKey": {"PK": "TENANT#t0", "SK": "METADATA"},
                }
            if call_count == 2:
                return {
                    "Items": [
                        {
                            "PK": "TENANT#t1",
                            "SK": "METADATA",
                            "tenant_id": "t1",
                            "status": "active",
                            "tier": "basic",
                        },
                        {
                            "PK": "TENANT#t2",
                            "SK": "METADATA",
                            "tenant_id": "t2",
                            "status": "active",
                            "tier": "basic",
                        },
                    ],
                }
            return {"Items": []}

        mock_table.scan.side_effect = mock_scan

        tenants = _get_active_tenants()

        # Should have all 3 tenants
        assert len(tenants) == 3
        assert call_count == 2


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

    with patch("src.billing.handler._dynamodb") as mock_ddb_resource:
        mock_table = MagicMock()
        mock_ddb_resource.Table.return_value = mock_table

        # CR003: billing now uses atomic update_item ADD; return Attributes so the
        # handler can extract totals for metric emission and budget enforcement.
        mock_table.update_item.return_value = {
            "Attributes": {
                "total_input_tokens": 2000,
                "total_output_tokens": 0,
                "total_cost_usd": 0.2,
            }
        }

        # mock_table.query (for invocations)
        mock_table.query.side_effect = [
            {
                "Items": [
                    {
                        "PK": f"TENANT#{tenant_id}",
                        "SK": f"INV#{ts1}#1",
                        "input_tokens": 1000,
                        "output_tokens": 0,
                    }
                ],
                "LastEvaluatedKey": {"PK": f"TENANT#{tenant_id}", "SK": f"INV#{ts1}#1"},
            },
            {
                "Items": [
                    {
                        "PK": f"TENANT#{tenant_id}",
                        "SK": f"INV#{ts2}#2",
                        "input_tokens": 1000,
                        "output_tokens": 0,
                    }
                ],
            },
        ]

        tenant = {"tenant_id": tenant_id, "tier": "basic", "app_id": app_id, "status": "active"}
        _process_tenant(tenant, yesterday)

        # CR003: billing now uses atomic ADD update_item instead of put_item.
        # Verify the day token total (2000) is passed as the ADD operand :di.
        assert mock_table.query.call_count == 2
        assert mock_table.update_item.called
        call_kwargs = mock_table.update_item.call_args[1]
        expr_vals = call_kwargs["ExpressionAttributeValues"]
        assert expr_vals[":di"] == 2000, f"Expected :di=2000, got {expr_vals.get(':di')}"


def test_platform_billing_status_pagination(mock_aws_clients: Any) -> None:
    """Verify that _handle_platform_billing_status paginates through billing summaries."""
    year_month = datetime.now(UTC).strftime("%Y-%m")

    caller = CallerIdentity(
        tenant_id="platform-admin",
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

    with patch("src.tenant_api.handler.TenantScopedDynamoDB") as mock_db_class:
        mock_db = MagicMock()
        mock_db_class.return_value = mock_db

        # mock scan_all for BILLING records
        mock_db.scan_all.return_value = [
            {
                "PK": "TENANT#t1",
                "SK": f"BILLING#{year_month}",
                "total_cost_usd": 10.0,
                "total_input_tokens": 1000,
                "total_output_tokens": 500,
            },
            {
                "PK": "TENANT#t2",
                "SK": f"BILLING#{year_month}",
                "total_cost_usd": 20.0,
                "total_input_tokens": 2000,
                "total_output_tokens": 1000,
            },
        ]

        result = _handle_platform_billing_status(caller, deps)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["tenantCount"] == 2
        assert body["totalCostUsd"] == 30.0
        assert body["totalTokens"] == 4500  # (1000+500) + (2000+1000) = 1500 + 3000 = 4500
        assert mock_db.scan_all.call_count == 1
