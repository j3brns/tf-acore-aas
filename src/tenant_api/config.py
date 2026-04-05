from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from src.tenant_api import constants, utils


@dataclass(frozen=True)
class TenantApiConfig:
    region: str
    platform_env: str
    tenants_table_name: str
    agents_table_name: str
    invocations_table_name: str
    event_bus_name: str
    audit_export_bucket: str | None
    audit_export_url_expiry_seconds: int
    api_key_secret_prefix: str
    tenant_mgmt_role_arn: str | None
    ops_locks_table_name: str
    runtime_region_param_name: str
    fallback_region_param_name: str
    failover_lock_name: str


def from_env(env: Mapping[str, str] | None = None) -> TenantApiConfig:
    source = env if env is not None else os.environ

    region = utils.str_or_none(source.get("AWS_REGION"))
    if region is None:
        raise ValueError("AWS_REGION is required")

    return TenantApiConfig(
        region=region,
        platform_env=utils.str_or_none(source.get("PLATFORM_ENV")) or "dev",
        tenants_table_name=utils.str_or_none(source.get(constants.TENANTS_TABLE_ENV))
        or "platform-tenants",
        agents_table_name=utils.str_or_none(source.get(constants.AGENTS_TABLE_ENV))
        or "platform-agents",
        invocations_table_name=utils.str_or_none(source.get(constants.INVOCATIONS_TABLE_ENV))
        or "platform-invocations",
        event_bus_name=utils.str_or_none(source.get(constants.EVENT_BUS_ENV)) or "default",
        audit_export_bucket=utils.str_or_none(source.get(constants.AUDIT_EXPORT_BUCKET_ENV)),
        audit_export_url_expiry_seconds=utils.coerce_positive_int(
            source.get("AUDIT_EXPORT_URL_EXPIRY_SECONDS"),
            default=constants.AUDIT_EXPORT_URL_EXPIRY_SECONDS,
        ),
        api_key_secret_prefix=utils.str_or_none(source.get(constants.API_KEY_SECRET_PREFIX_ENV))
        or "platform/tenants",
        tenant_mgmt_role_arn=utils.str_or_none(source.get(constants.TENANT_MGMT_ROLE_ARN_ENV)),
        ops_locks_table_name=utils.str_or_none(source.get(constants.OPS_LOCKS_TABLE_ENV))
        or constants.DEFAULT_OPS_LOCKS_TABLE,
        runtime_region_param_name=utils.str_or_none(source.get(constants.RUNTIME_REGION_PARAM_ENV))
        or constants.DEFAULT_RUNTIME_REGION_PARAM,
        fallback_region_param_name=utils.str_or_none(
            source.get(constants.FALLBACK_REGION_PARAM_ENV)
        )
        or constants.DEFAULT_FALLBACK_REGION_PARAM,
        failover_lock_name=utils.str_or_none(source.get(constants.FAILOVER_LOCK_NAME_ENV))
        or constants.DEFAULT_FAILOVER_LOCK_NAME,
    )


def current_config() -> TenantApiConfig:
    return from_env()
