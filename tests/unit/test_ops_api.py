from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import handler as tenant_api_handler
from tests.unit.test_tenant_api_handler import (
    FakeEvents,
    FakeMemoryProvisioner,
    FakeScopedDb,
    FakeSecretsManager,
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
        usage_client=FakeUsageClient(),
        memory_provisioner=FakeMemoryProvisioner(),
    )
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    monkeypatch.setenv("TENANTS_TABLE_NAME", "platform-tenants")
    monkeypatch.setenv("EVENT_BUS_NAME", "platform-bus")
    monkeypatch.setenv("TENANT_API_KEY_SECRET_PREFIX", "platform/tenants")
    monkeypatch.setattr(tenant_api_handler, "_dependencies", lambda: deps)
    monkeypatch.setattr(tenant_api_handler, "_db_for_tenant", lambda **_kwargs: db)
    monkeypatch.setattr(tenant_api_handler, "_now_utc", lambda: fixed_now)
    return {"db": db, "deps": deps}


def _ops_event(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": query,
        "body": None if body is None else json.dumps(body),
        "requestContext": {
            "authorizer": {
                "tenantid": "platform-admin",
                "roles": "Platform.Admin",
                "sub": "admin-123",
            }
        },
    }


def test_ops_top_tenants(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("GET", "/v1/platform/ops/top-tenants", query={"n": "3"}))
    assert response["statusCode"] == 200
    body = _body(response)
    assert len(body["tenants"]) == 3
    assert body["tenants"][0]["tenantId"] == "t-001"


def test_ops_security_events(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("GET", "/v1/platform/ops/security-events"))
    assert response["statusCode"] == 200
    body = _body(response)
    assert len(body["events"]) == 1
    assert body["events"][0]["type"] == "tenant_access_violation"


def test_ops_error_rate(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("GET", "/v1/platform/ops/error-rate", query={"minutes": "10"}))
    assert response["statusCode"] == 200
    body = _body(response)
    assert body["periodMinutes"] == 10
    assert "errorRate" in body


def test_ops_dlq_management(fake_state: dict[str, Any]) -> None:
    # Inspect
    response = _invoke(_ops_event("GET", "/v1/platform/ops/dlq/bridge-dlq"))
    assert response["statusCode"] == 200
    assert _body(response)["queueName"] == "bridge-dlq"

    # Redrive
    response = _invoke(_ops_event("POST", "/v1/platform/ops/dlq/bridge-dlq/redrive"))
    assert response["statusCode"] == 200
    assert _body(response)["status"] == "initiated"


def test_ops_tenant_management(fake_state: dict[str, Any]) -> None:
    # Sessions
    response = _invoke(_ops_event("GET", "/v1/platform/ops/tenants/t-001/sessions"))
    assert response["statusCode"] == 200
    assert _body(response)["tenantId"] == "t-001"

    # Suspend
    response = _invoke(
        _ops_event("POST", "/v1/platform/ops/tenants/t-001/suspend", body={"reason": "test"})
    )
    assert response["statusCode"] == 200
    assert _body(response)["status"] == "suspended"

    # Reinstate
    response = _invoke(_ops_event("POST", "/v1/platform/ops/tenants/t-001/reinstate"))
    assert response["statusCode"] == 200
    assert _body(response)["status"] == "active"


def test_ops_invocation_report(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("GET", "/v1/platform/ops/tenants/t-001/invocations"))
    assert response["statusCode"] == 200
    assert _body(response)["tenantId"] == "t-001"
    assert "totalInvocations" in _body(response)


def test_ops_service_health(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("GET", "/v1/platform/service-health"))
    assert response["statusCode"] == 200
    assert _body(response)["status"] == "healthy"


def test_ops_billing_status(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("GET", "/v1/platform/billing/status"))
    assert response["statusCode"] == 200
    assert _body(response)["status"] == "active"


def test_ops_fail_job(fake_state: dict[str, Any]) -> None:
    response = _invoke(
        _ops_event("POST", "/v1/platform/ops/jobs/job-123/fail", body={"reason": "manual fail"})
    )
    assert response["statusCode"] == 200
    assert _body(response)["jobId"] == "job-123"


def test_ops_page_security(fake_state: dict[str, Any]) -> None:
    response = _invoke(
        _ops_event(
            "POST",
            "/v1/platform/ops/security/page",
            body={"incident": "breach", "tenant": "t-1"},
        )
    )
    assert response["statusCode"] == 200
    assert _body(response)["status"] == "paged"
