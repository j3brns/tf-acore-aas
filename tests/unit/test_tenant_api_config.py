from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import config


def test_from_env_requires_region() -> None:
    with pytest.raises(ValueError, match="AWS_REGION is required"):
        config.from_env({})


def test_from_env_uses_defaults_for_optional_values() -> None:
    cfg = config.from_env({"AWS_REGION": "eu-west-2"})

    assert cfg.region == "eu-west-2"
    assert cfg.platform_env == "dev"
    assert cfg.tenants_table_name == "platform-tenants"
    assert cfg.agents_table_name == "platform-agents"
    assert cfg.invocations_table_name == "platform-invocations"
    assert cfg.event_bus_name == "default"
    assert cfg.audit_export_bucket is None
    assert cfg.audit_export_url_expiry_seconds == 3600
    assert cfg.api_key_secret_prefix == "platform/tenants"  # pragma: allowlist secret
    assert cfg.tenant_mgmt_role_arn is None
    assert cfg.ops_locks_table_name == "platform-ops-locks"
    assert cfg.runtime_region_param_name == "/platform/config/runtime-region"
    assert cfg.fallback_region_param_name == "/platform/config/fallback-region"
    assert cfg.failover_lock_name == "platform-runtime-failover"


def test_from_env_overrides_configured_values() -> None:
    cfg = config.from_env(
        {
            "AWS_REGION": "eu-central-1",
            "PLATFORM_ENV": "prod",
            "TENANTS_TABLE_NAME": "tenants-x",
            "AGENTS_TABLE_NAME": "agents-x",
            "INVOCATIONS_TABLE_NAME": "invocations-x",
            "EVENT_BUS_NAME": "bus-x",
            "AUDIT_EXPORT_BUCKET": "audit-bucket-x",
            "AUDIT_EXPORT_URL_EXPIRY_SECONDS": "1800",
            "TENANT_API_KEY_SECRET_PREFIX": "custom/prefix",  # pragma: allowlist secret
            "TENANT_MGMT_ROLE_ARN": "arn:aws:iam::111111111111:role/custom",
            "OPS_LOCKS_TABLE": "locks-x",
            "RUNTIME_REGION_PARAM": "/x/runtime",
            "FALLBACK_REGION_PARAM": "/x/fallback",
            "FAILOVER_LOCK_NAME": "lock-x",
        }
    )

    assert cfg.region == "eu-central-1"
    assert cfg.platform_env == "prod"
    assert cfg.tenants_table_name == "tenants-x"
    assert cfg.agents_table_name == "agents-x"
    assert cfg.invocations_table_name == "invocations-x"
    assert cfg.event_bus_name == "bus-x"
    assert cfg.audit_export_bucket == "audit-bucket-x"
    assert cfg.audit_export_url_expiry_seconds == 1800
    assert cfg.api_key_secret_prefix == "custom/prefix"
    assert cfg.tenant_mgmt_role_arn == "arn:aws:iam::111111111111:role/custom"
    assert cfg.ops_locks_table_name == "locks-x"
    assert cfg.runtime_region_param_name == "/x/runtime"
    assert cfg.fallback_region_param_name == "/x/fallback"
    assert cfg.failover_lock_name == "lock-x"
