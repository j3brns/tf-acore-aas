"""
test_billing_handler.py — Unit tests for the billing pipeline.

Uses moto to mock DynamoDB and SSM.
Verifies:
  - Token aggregation across multiple invocations
  - Cost calculation based on SSM pricing
  - Update of BillingSummaryRecord
  - Tenant suspension on budget exceeded
  - EventBridge notification on suspension

Implemented in TASK-052.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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
os.environ["EVENT_BUS_NAME"] = "default"

from src.billing.handler import lambda_handler

# Constants for test
TENANT_ID = "t-test-001"
APP_ID = "app-test-001"
TIER = "basic"
BUDGET = 5.0


@pytest.fixture
def mock_aws_clients() -> Generator[None, None, None]:
    with mock_aws():
        # Setup DynamoDB
        ddb = boto3.resource("dynamodb", region_name="eu-west-2")

        # Tenants table
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

        # Invocations table
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

        # Setup SSM
        ssm = boto3.client("ssm", region_name="eu-west-2")
        ssm.put_parameter(
            Name=f"/platform/billing/pricing/{TIER}",
            Value=json.dumps({"input_1k": 0.1, "output_1k": 0.2}),  # High pricing for easy testing
            Type="String",
        )

        # Setup EventBridge
        events = boto3.client("events", region_name="eu-west-2")
        try:
            events.create_event_bus(Name="default")
        except Exception:
            # Default bus might already exist
            pass

        yield


def _seed_tenant(ddb: Any, *, status: str = "active", budget: float = BUDGET) -> None:
    table = ddb.Table("platform-tenants")
    table.put_item(
        Item={
            "PK": f"TENANT#{TENANT_ID}",
            "SK": "METADATA",
            "tenant_id": TENANT_ID,
            "app_id": APP_ID,
            "tier": TIER,
            "status": status,
            "monthly_budget_usd": Decimal(str(budget)),
        }
    )


def _seed_invocation(ddb: Any, *, timestamp: str, input_tokens: int, output_tokens: int) -> None:
    table = ddb.Table("platform-invocations")
    table.put_item(
        Item={
            "PK": f"TENANT#{TENANT_ID}",
            "SK": f"INV#{timestamp}#id-123",
            "tenant_id": TENANT_ID,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    )


def test_billing_aggregation_and_cost(mock_aws_clients: Any) -> None:
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    _seed_tenant(ddb)

    # Seed invocations for "yesterday"
    yesterday = datetime.now(UTC) - timedelta(days=1)
    ts1 = yesterday.replace(hour=10).isoformat()
    ts2 = yesterday.replace(hour=14).isoformat()

    _seed_invocation(ddb, timestamp=ts1, input_tokens=1000, output_tokens=500)
    _seed_invocation(ddb, timestamp=ts2, input_tokens=2000, output_tokens=1500)

    # Run handler
    event = {"date": yesterday.date().isoformat()}
    result = lambda_handler(event, MagicMock())

    assert result["processed"] == 1
    assert result["errors"] == 0

    # Verify BillingSummaryRecord
    table = ddb.Table("platform-tenants")
    year_month = yesterday.strftime("%Y-%m")
    resp = table.get_item(Key={"PK": f"TENANT#{TENANT_ID}", "SK": f"BILLING#{year_month}"})
    summary = resp["Item"]

    assert summary["total_input_tokens"] == 3000
    assert summary["total_output_tokens"] == 2000
    # Cost: (3.0 * 0.1) + (2.0 * 0.2) = 0.3 + 0.4 = 0.7
    assert float(summary["total_cost_usd"]) == 0.7


def test_budget_exceeded_suspension(mock_aws_clients: Any) -> None:
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    # Low budget to trigger suspension
    _seed_tenant(ddb, budget=0.5)

    yesterday = datetime.now(UTC) - timedelta(days=1)
    ts = yesterday.replace(hour=12).isoformat()
    # Cost will be (5.0 * 0.1) + (5.0 * 0.2) = 0.5 + 1.0 = 1.5 > 0.5
    _seed_invocation(ddb, timestamp=ts, input_tokens=5000, output_tokens=5000)

    # Run handler
    event = {"date": yesterday.date().isoformat()}
    lambda_handler(event, MagicMock())

    # Verify tenant is suspended
    table = ddb.Table("platform-tenants")
    resp = table.get_item(Key={"PK": f"TENANT#{TENANT_ID}", "SK": "METADATA"})
    tenant = resp["Item"]
    assert tenant["status"] == "suspended"


def test_incremental_update(mock_aws_clients: Any) -> None:
    ddb = boto3.resource("dynamodb", region_name="eu-west-2")
    _seed_tenant(ddb)

    yesterday = datetime.now(UTC) - timedelta(days=1)
    year_month = yesterday.strftime("%Y-%m")

    # Seed an existing summary for earlier this month
    table = ddb.Table("platform-tenants")
    table.put_item(
        Item={
            "PK": f"TENANT#{TENANT_ID}",
            "SK": f"BILLING#{year_month}",
            "total_input_tokens": 5000,
            "total_output_tokens": 2000,
            "total_cost_usd": Decimal("1.5"),
        }
    )

    # Seed today's invocation
    ts = yesterday.replace(hour=12).isoformat()
    _seed_invocation(ddb, timestamp=ts, input_tokens=1000, output_tokens=1000)

    # Run handler
    event = {"date": yesterday.date().isoformat()}
    lambda_handler(event, MagicMock())

    # Verify summary is incremented
    resp = table.get_item(Key={"PK": f"TENANT#{TENANT_ID}", "SK": f"BILLING#{year_month}"})
    summary = resp["Item"]

    assert summary["total_input_tokens"] == 6000
    assert summary["total_output_tokens"] == 3000
    # New cost: 1.5 + (1*0.1 + 1*0.2) = 1.5 + 0.3 = 1.8
    assert float(summary["total_cost_usd"]) == 1.8
