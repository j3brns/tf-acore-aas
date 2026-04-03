from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from aws_lambda_powertools.utilities.parameters import SSMProvider


@dataclass(frozen=True)
class BillingIntegrations:
    ssm: Any
    events: Any
    cloudwatch: Any
    pricing_provider: SSMProvider


def build_billing_integrations(*, region: str) -> BillingIntegrations:
    ssm = boto3.client("ssm", region_name=region)
    return BillingIntegrations(
        ssm=ssm,
        events=boto3.client("events", region_name=region),
        cloudwatch=boto3.client("cloudwatch", region_name=region),
        pricing_provider=SSMProvider(boto3_client=ssm),
    )
