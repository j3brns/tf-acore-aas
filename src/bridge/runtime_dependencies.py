from __future__ import annotations

import os
from typing import Any

import boto3
import requests
from botocore.config import Config
from data_access import ControlPlaneDynamoDB, TenantCapabilityClient, TenantScopedDynamoDB
from data_access.models import AgentRecord, TenantContext, TenantTier

from src.bridge.config_provider import ConfigProvider, config_defaults, fetch_ssm_config
from src.bridge.constants import (
    AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS,
    AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS,
    AGENTS_TABLE,
    TENANTS_TABLE,
)
from src.bridge.discovery_service import resolve_agent_record as discovery_resolve_agent_record


def _aws_region() -> str:
    return os.environ["AWS_REGION"]


def get_capability_client() -> TenantCapabilityClient:
    return TenantCapabilityClient()


def get_ssm() -> Any:
    return boto3.client("ssm", region_name=_aws_region())


def get_sts() -> Any:
    return boto3.client("sts", region_name=_aws_region())


def get_cloudwatch() -> Any:
    return boto3.client("cloudwatch", region_name=_aws_region())


def get_http_session() -> requests.Session:
    return requests.Session()


def get_config(force_refresh: bool = False) -> dict[str, Any]:
    provider = ConfigProvider(
        fetcher=lambda: fetch_ssm_config(get_ssm(), get_http_session()),
        fallback_factory=config_defaults,
        ttl_seconds=60,
    )
    return provider.get(force_refresh=force_refresh)


def get_runtime_client(region: str, credentials: dict[str, Any] | None = None) -> Any:
    session_kwargs: dict[str, Any] = {"region_name": region}
    if credentials:
        session_kwargs.update(
            {
                "aws_access_key_id": credentials["AccessKeyId"],
                "aws_secret_access_key": credentials["SecretAccessKey"],
                "aws_session_token": credentials["SessionToken"],
            }
        )

    session = boto3.Session(**session_kwargs)
    client_kwargs: dict[str, Any] = {
        "service_name": "bedrock-agentcore",
        "region_name": region,
        "config": Config(
            connect_timeout=AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS,
            read_timeout=AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    }
    if os.environ.get("BEDROCK_AGENTCORE_DP_ENDPOINT"):
        client_kwargs["endpoint_url"] = os.environ.get("BEDROCK_AGENTCORE_DP_ENDPOINT")
    return session.client(**client_kwargs)


def get_platform_context() -> TenantContext:
    return TenantContext(
        tenant_id="platform",
        app_id="platform-bridge",
        tier=TenantTier.PREMIUM,
        sub="bridge-lambda",
    )


def get_tenant_record(tenant_context: TenantContext) -> dict[str, Any] | None:
    try:
        db = TenantScopedDynamoDB(tenant_context)
        return db.get_item(
            TENANTS_TABLE, {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": "METADATA"}
        )
    except Exception:
        return None


def get_agent_record(agent_name: str, agent_version: str | None = None) -> AgentRecord | None:
    return discovery_resolve_agent_record(
        ControlPlaneDynamoDB(get_platform_context()),
        agents_table=AGENTS_TABLE,
        agent_name=agent_name,
        agent_version=agent_version,
    )


def get_webhook_registration(
    tenant_context: TenantContext, webhook_id: str
) -> dict[str, Any] | None:
    try:
        db = TenantScopedDynamoDB(tenant_context)
        return db.get_item(
            TENANTS_TABLE,
            {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": f"WEBHOOK#{webhook_id}"},
        )
    except Exception:
        return None
