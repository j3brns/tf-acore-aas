from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import db_utils as tenant_api_db_utils
from src.tenant_api import handler as tenant_api_handler
from src.tenant_api import ops_control
from tests.unit.test_tenant_api_handler import (
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
)


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 2, 25, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def fake_state(monkeypatch: pytest.MonkeyPatch, fixed_now: datetime) -> dict[str, Any]:
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


def _ops_event(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
    *,
    roles: str = "Platform.Admin",
    tenant_id: str = "platform",
    sub: str = "admin-123",
) -> dict[str, Any]:
    return {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": query,
        "body": None if body is None else json.dumps(body),
        "requestContext": {
            "authorizer": {
                "tenantid": tenant_id,
                "roles": roles,
                "sub": sub,
                "appid": "app-admin",
            }
        },
    }


def test_ops_billing_status_returns_real_summary_shape(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-001", "BILLING#2026-02")] = {
        "PK": "TENANT#t-001",
        "SK": "BILLING#2026-02",
        "tenantId": "t-001",
        "totalInputTokens": 123,
        "totalOutputTokens": 456,
        "totalCostUsd": 7.89,
        "updatedAt": "2026-02-25T11:00:00Z",
    }

    response = _invoke(_ops_event("GET", "/v1/platform/billing/status"))

    assert response["statusCode"] == 200
    assert _body(response) == {
        "yearMonth": "2026-02",
        "summaries": [
            {
                "tenantId": "t-001",
                "totalInputTokens": 123,
                "totalOutputTokens": 456,
                "totalCostUsd": 7.89,
                "lastUpdated": "2026-02-25T11:00:00Z",
            }
        ],
    }


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/v1/platform/ops/top-tenants", "GET"),
        ("/v1/platform/ops/security-events", "GET"),
        ("/v1/platform/ops/error-rate", "GET"),
        ("/v1/platform/ops/dlq/bridge-dlq", "GET"),
        ("/v1/platform/ops/dlq/bridge-dlq/redrive", "POST"),
        ("/v1/platform/ops/tenants/t-001/invocations", "GET"),
        ("/v1/platform/ops/tenants/t-001/sessions", "GET"),
    ],
)
def test_de_scoped_ops_routes_do_not_expose_placeholder_success(
    fake_state: dict[str, Any], path: str, method: str
) -> None:
    del fake_state

    response = _invoke(_ops_event(method, path))

    assert response["statusCode"] == 405
    assert _body(response)["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_platform_agent_read_only_surface_is_bounded_to_authoritative_routes() -> None:
    assert ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES == {
        ("GET", "/v1/platform/agents"),
        ("GET", "/v1/platform/quota"),
        ("GET", "/v1/platform/billing/status"),
    }
    assert ("POST", "/v1/platform/failover") not in ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES
    assert ("POST", "/v1/platform/agents") not in ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES
    assert (
        ("GET", "/v1/platform/ops/top-tenants")
        not in ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES
    )
    assert (
        ("GET", "/v1/platform/ops/tenants/{tenant}/invocations")
        not in ops_control.READ_ONLY_PLATFORM_DIAGNOSTIC_ROUTES
    )
