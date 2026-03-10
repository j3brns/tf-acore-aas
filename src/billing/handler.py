"""
billing.handler — Daily Lambda aggregating token counts and applying pricing.

Processes the previous day's invocations for all active tenants.
Applies pricing from SSM based on tenant tier.
Updates BillingSummaryRecord (SK: BILLING#{yearMonth}) in platform-tenants.
Suspends tenants if monthly budget is exceeded.
Publishes platform.tenant.budget_exceeded to EventBridge.

Implemented in TASK-052.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from data_access.client import TenantScopedDynamoDB
from data_access.models import (
    BillingSummaryRecord,
    InvocationStatus,
    TenantContext,
    TenantStatus,
    TenantTier,
)

logger = Logger(service="billing")

# Table names from environment
TENANTS_TABLE = os.environ["TENANTS_TABLE_NAME"]
INVOCATIONS_TABLE = os.environ["INVOCATIONS_TABLE_NAME"]
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")

# Boto3 clients
_ssm = boto3.client("ssm", region_name=os.environ["AWS_REGION"])
_events = boto3.client("events", region_name=os.environ["AWS_REGION"])
_dynamodb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])


def _get_pricing(tier: str) -> dict[str, float]:
    """Fetch pricing for a tier from SSM."""
    path = f"/platform/billing/pricing/{tier}"
    try:
        response = _ssm.get_parameter(Name=path)
        value = response.get("Parameter", {}).get("Value")
        if not value:
            raise ValueError(f"Empty or missing Value in SSM parameter {path}")
        return json.loads(value)
    except Exception as e:
        logger.error(f"Failed to fetch pricing for tier={tier}: {e}")
        # Default fallback pricing (conservative)
        return {"input_1k": 0.01, "output_1k": 0.03}


def _calculate_cost(input_tokens: int, output_tokens: int, pricing: dict[str, float]) -> float:
    """Calculate cost in USD."""
    input_cost = (input_tokens / 1000.0) * pricing.get("input_1k", 0.0)
    output_cost = (output_tokens / 1000.0) * pricing.get("output_1k", 0.0)
    return input_cost + output_cost


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _get_active_tenants() -> list[dict[str, Any]]:
    """Scan platform-tenants for active/suspended tenants."""
    table = _dynamodb.Table(TENANTS_TABLE)
    response = table.scan(
        FilterExpression=Key("SK").eq("METADATA")
        & (Key("status").eq(TenantStatus.ACTIVE) | Key("status").eq(TenantStatus.SUSPENDED))
    )
    return response.get("Items", [])


def _process_tenant(tenant: dict[str, Any], date_to_process: datetime) -> None:
    tenant_id = tenant["tenant_id"]
    tier = tenant["tier"]
    budget = float(tenant.get("monthly_budget_usd", 0.0))
    app_id = tenant.get("app_id", "unknown")

    # Day window
    start_time = date_to_process.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end_time = date_to_process.replace(
        hour=23, minute=59, second=59, microsecond=999999
    ).isoformat()
    year_month = date_to_process.strftime("%Y-%m")

    logger.append_keys(tenantid=tenant_id, appid=app_id)
    logger.info(f"Processing billing for {tenant_id} on {date_to_process.date()}")

    # Context for data-access-lib
    ctx = TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=TenantTier(tier),
        sub="billing-pipeline",
    )
    db = TenantScopedDynamoDB(ctx, dynamodb_resource=_dynamodb)

    # 1. Query invocations for the day
    # SK starts with INV#timestamp
    result = db.query(
        INVOCATIONS_TABLE,
        sk_condition=Key("SK").between(f"INV#{start_time}", f"INV#{end_time}"),
    )

    day_input = sum(int(inv.get("input_tokens", 0)) for inv in result.items)
    day_output = sum(int(inv.get("output_tokens", 0)) for inv in result.items)

    # 2. Apply pricing
    pricing = _get_pricing(tier)
    day_cost = _calculate_cost(day_input, day_output, pricing)

    # 3. Update monthly summary
    # SK: BILLING#{yearMonth}
    summary_key = {"PK": f"TENANT#{tenant_id}", "SK": f"BILLING#{year_month}"}

    current_summary = db.get_item(TENANTS_TABLE, summary_key)

    total_input = day_input
    if current_summary:
        total_input += int(current_summary.get("total_input_tokens", 0))

    total_output = day_output
    if current_summary:
        total_output += int(current_summary.get("total_output_tokens", 0))

    total_cost = day_cost
    if current_summary:
        total_cost += float(current_summary.get("total_cost_usd", 0.0))

    new_summary = {
        "PK": f"TENANT#{tenant_id}",
        "SK": f"BILLING#{year_month}",
        "tenant_id": tenant_id,
        "year_month": year_month,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost_usd": Decimal(str(round(total_cost, 4))),
        "last_updated": _iso_now(),
    }
    db.put_item(TENANTS_TABLE, new_summary)

    # 4. Check budget
    if budget > 0 and total_cost > budget:
        if tenant["status"] == TenantStatus.ACTIVE:
            logger.warning(
                f"Tenant {tenant_id} exceeded budget {budget} (cost={total_cost}). Suspending."
            )

            # Suspend tenant
            table = _dynamodb.Table(TENANTS_TABLE)
            table.update_item(
                Key={"PK": f"TENANT#{tenant_id}", "SK": "METADATA"},
                UpdateExpression="SET #s = :s, updated_at = :u",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": TenantStatus.SUSPENDED,
                    ":u": _iso_now(),
                },
                ConditionExpression="attribute_exists(PK)",
            )

            # Publish event
            _events.put_events(
                Entries=[
                    {
                        "Source": "platform.billing",
                        "DetailType": "platform.tenant.budget_exceeded",
                        "Detail": json.dumps(
                            {
                                "tenantId": tenant_id,
                                "tier": tier,
                                "budget": budget,
                                "cost": total_cost,
                                "yearMonth": year_month,
                            }
                        ),
                        "EventBusName": EVENT_BUS_NAME,
                    }
                ]
            )
        else:
            logger.info(
                f"Tenant {tenant_id} already suspended (cost={total_cost}, budget={budget})"
            )


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Lambda entry point."""
    logger.info("Billing pipeline started")

    # Determine date to process (yesterday by default)
    # Allows manual override via event e.g. {"date": "2026-03-07"}
    if "date" in event:
        date_to_process = datetime.fromisoformat(event["date"]).replace(tzinfo=UTC)
    else:
        date_to_process = datetime.now(UTC) - timedelta(days=1)

    tenants = _get_active_tenants()
    logger.info(f"Found {len(tenants)} active/suspended tenants to process")

    processed = 0
    errors = 0

    for tenant in tenants:
        try:
            _process_tenant(tenant, date_to_process)
            processed += 1
        except Exception as e:
            logger.exception(f"Failed to process tenant {tenant.get('tenant_id')}: {e}")
            errors += 1

    logger.info(f"Billing pipeline complete. Processed={processed}, Errors={errors}")
    return {
        "status": "success",
        "processed": processed,
        "errors": errors,
        "date": date_to_process.date().isoformat(),
    }
