from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.authoriser.handler import is_admin_route, is_platform_route
from src.tenant_api import db_utils as tenant_api_db_utils
from src.tenant_api import handler as tenant_api_handler
from src.tenant_api import ops_control
from tests.unit.test_ops_api import (
    FakeDynamoDbResource,
    FakeEvents,
    FakeLambdaClient,
    FakeMemoryProvisioner,
    FakePlatformQuotaClient,
    FakeScopedDb,
    FakeSecretsManager,
    FakeSsm,
    FakeUsageClient,
    _body,
    _invoke,
    _ops_event,
)


def _load_openapi() -> dict:
    spec_path = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"
    with spec_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _method_arn(method: str, path: str) -> str:
    normalized = path.lstrip("/")
    return f"arn:aws:execute-api:eu-west-2:123456789012:api/dev/{method}/{normalized}"


@pytest.fixture
def fake_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fixed_now = datetime(2026, 2, 25, 12, 0, 0, tzinfo=UTC)
    db = FakeScopedDb()
    deps = tenant_api_handler.TenantApiDependencies(
        secretsmanager=FakeSecretsManager(),
        events=FakeEvents(),
        dynamodb=FakeDynamoDbResource(),
        ssm=FakeSsm(),
        awslambda=FakeLambdaClient(),
        usage_client=FakeUsageClient(),
        memory_provisioner=FakeMemoryProvisioner(),
        platform_quota_client=FakePlatformQuotaClient(),
    )
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    monkeypatch.setenv("TENANTS_TABLE_NAME", "platform-tenants")
    monkeypatch.setenv("INVOCATIONS_TABLE_NAME", "platform-invocations")
    monkeypatch.setenv("EVENT_BUS_NAME", "platform-bus")
    monkeypatch.setenv("AUDIT_EXPORT_BUCKET", "platform-audit-exports")
    monkeypatch.setenv("AUDIT_EXPORT_URL_EXPIRY_SECONDS", "1800")
    monkeypatch.setenv("TENANT_API_KEY_SECRET_PREFIX", "platform/tenants")
    monkeypatch.setenv(
        "TENANT_MGMT_ROLE_ARN",
        "arn:aws:iam::111111111111:role/platform-tenant-mgmt-dev",
    )
    monkeypatch.setenv("OPS_LOCKS_TABLE", "platform-ops-locks")
    monkeypatch.setenv("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")
    monkeypatch.setenv("FALLBACK_REGION_PARAM", "/platform/config/fallback-region")
    monkeypatch.setattr(tenant_api_handler, "_dependencies", lambda: deps)
    monkeypatch.setattr(tenant_api_handler.db_factory, "db_for_tenant", lambda **_kwargs: db)
    monkeypatch.setattr(
        tenant_api_handler.db_factory, "control_plane_db", lambda *_args, **_kwargs: db
    )
    monkeypatch.setattr(tenant_api_db_utils, "db_for_tenant", lambda **_kwargs: db)
    monkeypatch.setattr(tenant_api_db_utils, "control_plane_db", lambda *_args, **_kwargs: db)
    monkeypatch.setattr(tenant_api_handler.utils, "_OVERRIDE_NOW", fixed_now)
    return {"db": db, "deps": deps}


def test_health_route_stays_public_across_openapi_and_authoriser() -> None:
    spec = _load_openapi()
    health_get = spec["paths"]["/v1/health"]["get"]

    assert health_get["security"] == []
    assert is_admin_route(_method_arn("GET", "/v1/health")) is False
    assert is_platform_route(_method_arn("GET", "/v1/health")) is False


def test_platform_read_only_surface_is_declared_and_admin_protected() -> None:
    spec = _load_openapi()
    paths = spec["paths"]

    for method, path in ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES:
        operation = paths[path][method.lower()]
        assert operation.get("security") != []
        assert operation["x-required-roles"] == ["Platform.Admin", "Platform.Operator"]


def test_non_platform_tenants_cannot_access_platform_diagnostic_surface(
    fake_state: dict[str, object],
) -> None:
    del fake_state

    for path in ("/v1/platform/quota", "/v1/platform/billing/status", "/v1/platform/agents"):
        response = _invoke(
            _ops_event(
                "GET",
                path,
                roles="Tenant.Admin",
                tenant_id="t-test-001",
                sub="tenant-admin",
            )
        )
        assert response["statusCode"] == 403
        assert _body(response)["error"]["code"] == "FORBIDDEN"
