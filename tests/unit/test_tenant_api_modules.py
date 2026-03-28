from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_tenant_api_handler import (
    FakeEvents,
    FakeLambdaClient,
    FakeMemoryProvisioner,
    FakePlatformQuotaClient,
    FakeScopedDb,
    FakeSecretsManager,
    FakeSsm,
    FakeUsageClient,
)

from src.tenant_api import agent_registry, ops_control, tenant_lifecycle, webhook_registry
from src.tenant_api import handler as tenant_api_handler


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 2, 25, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def module_state(monkeypatch: pytest.MonkeyPatch, fixed_now: Any) -> dict[str, Any]:
    db = FakeScopedDb()
    deps = tenant_api_handler.TenantApiDependencies(
        secretsmanager=FakeSecretsManager(),
        events=FakeEvents(),
        dynamodb=None,
        ssm=FakeSsm(),
        awslambda=FakeLambdaClient(),
        usage_client=FakeUsageClient(),
        memory_provisioner=FakeMemoryProvisioner(),
        platform_quota_client=FakePlatformQuotaClient(),
    )
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    monkeypatch.setenv("TENANTS_TABLE_NAME", "platform-tenants")
    monkeypatch.setenv("AGENTS_TABLE_NAME", "platform-agents")
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
    monkeypatch.setattr(tenant_api_handler, "_db_for_tenant", lambda **_kwargs: db)
    monkeypatch.setattr(tenant_api_handler, "_now_utc", lambda: fixed_now)
    return {"db": db, "deps": deps}


def _caller(
    *,
    tenant_id: str | None = "t-admin",
    roles: list[str] | None = None,
    app_id: str = "app-admin",
) -> tenant_api_handler.CallerIdentity:
    return tenant_api_handler.CallerIdentity(
        tenant_id=tenant_id,
        app_id=app_id,
        tier="premium",
        sub="user-123",
        roles=frozenset(roles or ["Platform.Admin"]),
        usage_identifier_key=None,
    )


def _event(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "path": path,
        "httpMethod": method,
        "body": None if body is None else json.dumps(body),
        "queryStringParameters": {},
    }


def test_agent_registry_dispatch_registers_agent(module_state: dict[str, Any]) -> None:
    response = agent_registry.dispatch_routes(
        "/v1/platform/agents",
        "POST",
        _event(
            "/v1/platform/agents",
            "POST",
            {"agentName": "echo-agent", "version": "1.0.0"},
        ),
        _caller(),
        module_state["deps"],
    )

    assert response is not None
    assert response["statusCode"] == 201
    stored = module_state["db"].items[("AGENT#echo-agent", "VERSION#1.0.0")]
    assert stored["status"] == "built"


def test_ops_control_dispatches_platform_quota(module_state: dict[str, Any]) -> None:
    response = ops_control.dispatch_platform_admin_routes(
        "/v1/platform/quota",
        "GET",
        _event("/v1/platform/quota"),
        _caller(),
        module_state["deps"],
    )

    assert response is not None
    body = json.loads(response["body"])
    assert body["utilisation"][0]["region"] == "eu-west-1"


def test_tenant_lifecycle_dispatch_creates_tenant(module_state: dict[str, Any]) -> None:
    response = tenant_lifecycle.dispatch_routes(
        "/v1/tenants",
        "POST",
        _event(
            "/v1/tenants",
            "POST",
            {
                "tenantId": "tenant-mod-001",
                "appId": "app-001",
                "displayName": "Acme Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
            },
        ),
        _caller(),
        module_state["deps"],
        None,
    )

    assert response is not None
    assert response["statusCode"] == 201
    assert ("TENANT#tenant-mod-001", "METADATA") in module_state["db"].items
    assert len(module_state["deps"].secretsmanager.policy_calls) == 1


def test_webhook_registry_dispatch_registers_webhook(module_state: dict[str, Any]) -> None:
    module_state["db"].items[("TENANT#t-001", "METADATA")] = {
        "PK": "TENANT#t-001",
        "SK": "METADATA",
        "tenantId": "t-001",
        "appId": "app-001",
        "displayName": "Acme Ltd",
        "tier": "standard",
        "status": "active",
    }
    response = webhook_registry.dispatch_routes(
        "/v1/webhooks",
        "POST",
        _event(
            "/v1/webhooks",
            "POST",
            {"callbackUrl": "https://example.com/hook", "events": ["job.completed"]},
        ),
        _caller(tenant_id="t-001", roles=["SelfService.Admin"]),
        module_state["deps"],
    )

    assert response is not None
    assert response["statusCode"] == 201
    webhook_keys = [
        key
        for key in module_state["db"].items
        if key[0] == "TENANT#t-001" and key[1].startswith("WEBHOOK#")
    ]
    assert webhook_keys
