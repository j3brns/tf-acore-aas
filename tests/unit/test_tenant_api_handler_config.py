from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import dependency_factories
from src.tenant_api import handler as tenant_api_handler


def test_dependencies_uses_central_config_region(monkeypatch) -> None:
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        tenant_api_handler.config,
        "current_config",
        lambda: tenant_api_handler.config.TenantApiConfig(
            region="eu-west-2",
            platform_env="dev",
            tenants_table_name="platform-tenants",
            agents_table_name="platform-agents",
            invocations_table_name="platform-invocations",
            event_bus_name="platform-bus",
            audit_export_bucket="platform-audit-exports",
            audit_export_url_expiry_seconds=1800,
            api_key_secret_prefix="platform/tenants",  # pragma: allowlist secret
            tenant_mgmt_role_arn="arn:aws:iam::111111111111:role/platform-tenant-mgmt-dev",
            ops_locks_table_name="platform-ops-locks",
            runtime_region_param_name="/platform/config/runtime-region",
            fallback_region_param_name="/platform/config/fallback-region",
            failover_lock_name="platform-runtime-failover",
        ),
    )
    monkeypatch.setattr(
        dependency_factories,
        "build_tenant_api_dependencies",
        lambda *, region: captured.setdefault("region", region) or object(),
    )

    tenant_api_handler._dependencies()

    assert captured["region"] == "eu-west-2"
