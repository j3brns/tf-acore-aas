from __future__ import annotations

from datetime import timedelta
from typing import Any

import boto3

from src.tenant_api import utils
from src.tenant_api.constants import (
    AGENTCORE_CONCURRENT_SESSIONS_METRIC,
    AGENTCORE_CONCURRENT_SESSIONS_NAMESPACE,
    AGENTCORE_QUOTA_LOOKBACK_MINUTES,
    AGENTCORE_QUOTA_NAME,
)
from src.tenant_api.models import TenantApiDependencies


class _NoopUsageClient:
    def get_tenant_usage(self, *, tenant_id: str, app_id: str | None) -> dict[str, Any]:
        return {"tenantId": tenant_id, "appId": app_id}


class _NoopMemoryProvisioner:
    def provision(self, *, tenant_id: str, app_id: str) -> dict[str, Any]:
        return {}


class _AwsPlatformQuotaClient:
    def __init__(self, session: Any) -> None:
        self._session = session

    def get_utilisation(
        self,
        *,
        active_region: str,
        fallback_region: str | None,
    ) -> list[dict[str, Any]]:
        regions: list[str] = []
        for region in (active_region, fallback_region):
            if region and region not in regions:
                regions.append(region)

        return [self._build_region_entry(region) for region in regions]

    def _build_region_entry(self, region: str) -> dict[str, Any]:
        current_value = self._current_sessions(region)
        limit = self._quota_limit(region)
        utilisation = 0.0 if limit <= 0 else round((current_value / limit) * 100, 2)
        return {
            "region": region,
            "quotaName": AGENTCORE_CONCURRENT_SESSIONS_METRIC,
            "currentValue": current_value,
            "limit": limit,
            "utilisationPercentage": utilisation,
        }

    def _current_sessions(self, region: str) -> float:
        cloudwatch = self._session.client("cloudwatch", region_name=region)
        end_time = utils.now_utc()
        start_time = end_time - timedelta(minutes=AGENTCORE_QUOTA_LOOKBACK_MINUTES)
        response = cloudwatch.get_metric_statistics(
            Namespace=AGENTCORE_CONCURRENT_SESSIONS_NAMESPACE,
            MetricName=AGENTCORE_CONCURRENT_SESSIONS_METRIC,
            StartTime=start_time,
            EndTime=end_time,
            Period=60,
            Statistics=["Maximum"],
        )
        datapoints = response.get("Datapoints", [])
        if not datapoints:
            return 0.0
        return max(float(point.get("Maximum", 0.0)) for point in datapoints)

    def _quota_limit(self, region: str) -> float:
        service_quotas = self._session.client("service-quotas", region_name=region)
        next_token: str | None = None

        while True:
            request: dict[str, Any] = {"ServiceCode": "bedrock-agentcore"}
            if next_token:
                request["NextToken"] = next_token

            response = service_quotas.list_service_quotas(**request)
            for quota in response.get("Quotas", []):
                if quota.get("QuotaName") == AGENTCORE_QUOTA_NAME:
                    return float(quota.get("Value", 0.0))

            next_token = response.get("NextToken")
            if not next_token:
                break

        return self._documented_default_limit(region)

    @staticmethod
    def _documented_default_limit(region: str) -> float:
        return 1000.0 if region == "us-east-1" else 500.0


def build_tenant_api_dependencies(*, region: str) -> TenantApiDependencies:
    session = boto3.session.Session(region_name=region)
    return TenantApiDependencies(
        secretsmanager=session.client("secretsmanager"),
        events=session.client("events"),
        ssm=session.client("ssm"),
        awslambda=session.client("lambda"),
        usage_client=_NoopUsageClient(),
        memory_provisioner=_NoopMemoryProvisioner(),
        platform_quota_client=_AwsPlatformQuotaClient(session),
    )
