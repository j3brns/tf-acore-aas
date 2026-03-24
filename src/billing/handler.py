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
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Attr, Key
from data_access.client import TenantScopedDynamoDB
from data_access.models import (
    TenantContext,
    TenantStatus,
    TenantTier,
)

logger = Logger(service="billing")
tracer = Tracer()

# Table names from environment
TENANTS_TABLE = os.environ["TENANTS_TABLE_NAME"]
INVOCATIONS_TABLE = os.environ["INVOCATIONS_TABLE_NAME"]
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")

# Boto3 clients — lazy initialisation (matches handler convention; avoids import-time failures)
_ssm = None
_events = None
_dynamodb = None
_cloudwatch = None


def _aws_region() -> str:
    return os.environ["AWS_REGION"]


def _get_ssm() -> Any:
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm", region_name=_aws_region())
    return _ssm


def _get_events() -> Any:
    global _events
    if _events is None:
        _events = boto3.client("events", region_name=_aws_region())
    return _events


def _get_dynamodb() -> Any:
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=_aws_region())
    return _dynamodb


def _get_cloudwatch() -> Any:
    global _cloudwatch
    if _cloudwatch is None:
        _cloudwatch = boto3.client("cloudwatch", region_name=_aws_region())
    return _cloudwatch


class PricingResolutionError(RuntimeError):
    """Raised when billing pricing configuration is unavailable or invalid."""


def _get_pricing(tier: str) -> dict[str, float]:
    """Fetch pricing for a tier from SSM."""
    path = f"/platform/billing/pricing/{tier}"
    try:
        response = _get_ssm().get_parameter(Name=path)
        value = response.get("Parameter", {}).get("Value")
    except Exception as exc:
        raise PricingResolutionError(f"Failed to fetch pricing parameter {path} from SSM") from exc

    if not value:
        raise PricingResolutionError(f"Pricing parameter {path} is empty or missing a value")

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise PricingResolutionError(f"Pricing parameter {path} contains malformed JSON") from exc

    if not isinstance(parsed, dict):
        raise PricingResolutionError(
            f"Pricing parameter {path} must be a JSON object, got {type(parsed).__name__}"
        )

    pricing: dict[str, float] = {}
    for field in ("input_1k", "output_1k"):
        raw_value = parsed.get(field)
        if raw_value is None:
            raise PricingResolutionError(f"Pricing parameter {path} is missing {field}")
        try:
            pricing[field] = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise PricingResolutionError(
                f"Pricing parameter {path} has non-numeric {field}: {raw_value!r}"
            ) from exc

    return pricing


def _calculate_cost(input_tokens: int, output_tokens: int, pricing: dict[str, float]) -> float:
    """Calculate cost in USD."""
    input_cost = (input_tokens / 1000.0) * pricing["input_1k"]
    output_cost = (output_tokens / 1000.0) * pricing["output_1k"]
    return input_cost + output_cost


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _get_active_tenants() -> list[dict[str, Any]]:
    """Scan platform-tenants for active/suspended tenants."""
    # System context for admin scan
    ctx = TenantContext(
        tenant_id="system",
        app_id="system",
        tier=TenantTier.PREMIUM,
        sub="billing-pipeline",
    )
    db = TenantScopedDynamoDB(ctx, dynamodb_resource=_get_dynamodb())
    # CR001: FilterExpression requires Attr() conditions, not Key() conditions.
    # Key() is only valid in KeyConditionExpression.
    return db.scan_all(
        TENANTS_TABLE,
        filter_expression=Attr("SK").eq("METADATA")
        & (Attr("status").eq(TenantStatus.ACTIVE) | Attr("status").eq(TenantStatus.SUSPENDED)),
    )


def _process_tenant(tenant: dict[str, Any], date_to_process: datetime) -> None:
    tenant_id = tenant["tenant_id"]
    tier = tenant["tier"]
    budget = float(tenant.get("monthly_budget_usd", 0.0))
    app_id = tenant.get("app_id", "unknown")
    pricing_path = f"/platform/billing/pricing/{tier}"

    # Day window
    start_time = date_to_process.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end_time = date_to_process.replace(
        hour=23, minute=59, second=59, microsecond=999999
    ).isoformat()
    year_month = date_to_process.strftime("%Y-%m")

    logger.append_keys(tenantid=tenant_id, appid=app_id)
    # CR006: Use structured kwargs instead of f-string interpolation.
    logger.info("Processing billing for tenant", date=str(date_to_process.date()))

    # Context for data-access-lib
    ctx = TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=TenantTier(tier),
        sub="billing-pipeline",
    )
    db = TenantScopedDynamoDB(ctx, dynamodb_resource=_get_dynamodb())

    # 1. Query invocations for the day
    # SK starts with INV#timestamp
    invocations = db.query_all(
        INVOCATIONS_TABLE,
        sk_condition=Key("SK").between(f"INV#{start_time}", f"INV#{end_time}"),
    )

    day_input = sum(int(inv.get("input_tokens", 0)) for inv in invocations)
    day_output = sum(int(inv.get("output_tokens", 0)) for inv in invocations)

    # 2. Apply pricing
    try:
        pricing = _get_pricing(tier)
    except PricingResolutionError as exc:
        logger.exception(
            "Billing pricing resolution failed",
            extra={"pricing_path": pricing_path, "tier": tier},
        )
        raise PricingResolutionError(f"Unable to price tenant {tenant_id}") from exc
    day_cost = _calculate_cost(day_input, day_output, pricing)

    # 3. Update monthly summary atomically.
    # CR003: Use a single atomic ADD + SET expression instead of read-modify-write.
    # DynamoDB ADD initialises missing numeric attributes to 0 before adding, so
    # the first write of the month is handled correctly without a pre-read.
    # ReturnValues=ALL_NEW (set by TenantScopedDynamoDB.update_item) gives us the
    # running totals for metric emission and budget enforcement below.
    summary_key = {"PK": f"TENANT#{tenant_id}", "SK": f"BILLING#{year_month}"}
    update_response = db.update_item(
        TENANTS_TABLE,
        summary_key,
        "SET tenant_id = :tid, year_month = :ym, last_updated = :lu "
        "ADD total_input_tokens :di, total_output_tokens :do, total_cost_usd :dc",
        {
            ":tid": tenant_id,
            ":ym": year_month,
            ":lu": _iso_now(),
            ":di": day_input,
            ":do": day_output,
            ":dc": Decimal(str(round(day_cost, 4))),
        },
    )
    updated = update_response.get("Attributes", {})
    total_input = int(updated.get("total_input_tokens", day_input))
    total_output = int(updated.get("total_output_tokens", day_output))
    total_cost = float(updated.get("total_cost_usd", day_cost))

    # 4. Emit metrics for observability
    # Monthly cost metric used for per-tenant budget alarms
    try:
        dimensions = [
            {"Name": "TenantId", "Value": tenant_id},
            {"Name": "Tier", "Value": tier},
        ]
        _get_cloudwatch().put_metric_data(
            Namespace="Platform/Billing",
            MetricData=[
                {
                    "MetricName": "MonthlyCost",
                    "Value": total_cost,
                    "Unit": "None",
                    "Dimensions": dimensions,
                },
                {
                    "MetricName": "DailyCost",
                    "Value": day_cost,
                    "Unit": "None",
                    "Dimensions": dimensions,
                },
                {
                    "MetricName": "InputTokens",
                    "Value": float(total_input),
                    "Unit": "None",
                    "Dimensions": dimensions,
                },
                {
                    "MetricName": "OutputTokens",
                    "Value": float(total_output),
                    "Unit": "None",
                    "Dimensions": dimensions,
                },
            ],
        )
    except Exception as exc:
        # CR006: structured logging
        logger.warning("Failed to emit cost metrics", exc_info=exc)

    # 5. Check budget
    if budget > 0 and total_cost > budget:
        if tenant["status"] == TenantStatus.ACTIVE:
            # CR006: structured kwargs
            logger.warning(
                "Tenant exceeded monthly budget; suspending",
                budget=budget,
                cost=total_cost,
            )

            # CR002: Use TenantScopedDynamoDB (data-access-lib) instead of raw boto3.
            db.update_item(
                TENANTS_TABLE,
                {"PK": f"TENANT#{tenant_id}", "SK": "METADATA"},
                "SET #s = :s, updated_at = :u",
                {":s": TenantStatus.SUSPENDED, ":u": _iso_now()},
                expression_attribute_names={"#s": "status"},
                condition_expression="attribute_exists(PK)",
            )

            # Publish event
            _get_events().put_events(
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
            # CR006: structured kwargs
            logger.info("Tenant already suspended", cost=total_cost, budget=budget)


@tracer.capture_lambda_handler
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
    # CR006: structured kwargs
    logger.info("Active/suspended tenants found", tenant_count=len(tenants))

    processed = 0
    errors = 0

    for tenant in tenants:
        try:
            _process_tenant(tenant, date_to_process)
            processed += 1
        except Exception:
            logger.exception(
                "Failed to process tenant billing",
                tenant_id=tenant.get("tenant_id"),
            )
            errors += 1

    status = "success" if errors == 0 else "partial_failure"
    # CR006: structured kwargs
    logger.info("Billing pipeline complete", processed=processed, errors=errors)
    return {
        "status": status,
        "processed": processed,
        "errors": errors,
        "date": date_to_process.date().isoformat(),
    }
