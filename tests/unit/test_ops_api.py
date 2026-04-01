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
    monkeypatch.setenv("EVENT_BUS_NAME", "platform-bus")
    monkeypatch.setenv("TENANT_API_KEY_SECRET_PREFIX", "platform/tenants")
    monkeypatch.setattr(tenant_api_handler, "_dependencies", lambda: deps)
    monkeypatch.setattr(tenant_api_handler, "_db_for_tenant", lambda **_kwargs: db)
    monkeypatch.setattr(tenant_api_handler, "_control_plane_db", lambda *_args, **_kwargs: db)
    monkeypatch.setattr(tenant_api_handler, "_now_utc", lambda: fixed_now)
    return {"db": db, "deps": deps}


def _seed_tenant(
    fake_state: dict[str, Any],
    tenant_id: str,
    *,
    status: str = "active",
    tier: str = "standard",
) -> None:
    """Seed a tenant record into the fake DB."""
    fake_state["db"].items[(f"TENANT#{tenant_id}", "METADATA")] = {
        "PK": f"TENANT#{tenant_id}",
        "SK": "METADATA",
        "tenantId": tenant_id,
        "appId": f"app-{tenant_id}",
        "displayName": f"Test tenant {tenant_id}",
        "tier": tier,
        "status": status,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }


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


def _last_event_detail(fake_state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    calls = fake_state["deps"].events.calls
    assert calls, "expected EventBridge put_events call"
    entry = calls[-1]["Entries"][0]
    return entry["DetailType"], json.loads(entry["Detail"])


def _event_count(fake_state: dict[str, Any]) -> int:
    return len(fake_state["deps"].events.calls)


# ---------------------------------------------------------------------------
# Existing stub-based ops routes (unchanged behaviour)
# ---------------------------------------------------------------------------


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


def test_ops_invocation_report(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("GET", "/v1/platform/ops/tenants/t-001/invocations"))
    assert response["statusCode"] == 200
    assert _body(response)["tenantId"] == "t-001"
    assert "totalInvocations" in _body(response)


def test_ops_service_health(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("GET", "/v1/platform/service-health"))
    assert response["statusCode"] == 200
    assert _body(response)["status"] == "healthy"
    assert _body(response)["audit"]["actorTenantId"] == "platform"


def test_ops_platform_routes_require_platform_tenant_context(fake_state: dict[str, Any]) -> None:
    event = _ops_event("GET", "/v1/platform/service-health")
    event["requestContext"]["authorizer"]["tenantid"] = "t-customer-001"

    response = _invoke(event)

    assert response["statusCode"] == 403


def test_ops_billing_status(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("GET", "/v1/platform/billing/status"))
    assert response["statusCode"] == 200
    assert _body(response)["status"] == "active"


def test_ops_tenant_sessions(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("GET", "/v1/platform/ops/tenants/t-001/sessions"))
    assert response["statusCode"] == 200
    assert _body(response)["tenantId"] == "t-001"


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


# ---------------------------------------------------------------------------
# Suspend tenant — authorized flow
# ---------------------------------------------------------------------------


def test_suspend_tenant_success(fake_state: dict[str, Any]) -> None:
    _seed_tenant(fake_state, "t-001")

    response = _invoke(
        _ops_event(
            "POST",
            "/v1/platform/ops/tenants/t-001/suspend",
            body={"reason": "budget exceeded"},
        )
    )
    assert response["statusCode"] == 200
    body = _body(response)
    assert body["tenantId"] == "t-001"
    assert body["status"] == "suspended"
    assert body["reason"] == "budget exceeded"

    # Verify DB was updated
    record = fake_state["db"].items[("TENANT#t-001", "METADATA")]
    assert record["status"] == "suspended"

    # Verify response audit envelope
    assert body["audit"]["actorTenantId"] == "platform"
    assert body["audit"]["operationType"] == "tenant_suspend"
    assert body["audit"]["targetTenantId"] == "t-001"

    # Verify EventBridge audit event
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.suspended"
    assert detail["targetTenantId"] == "t-001"
    assert detail["actorSub"] == "admin-123"
    assert detail["operationType"] == "tenant_suspend"
    assert detail["outcome"] == "success"
    assert detail["reason"] == "budget exceeded"


def test_suspend_tenant_missing_reason(fake_state: dict[str, Any]) -> None:
    _seed_tenant(fake_state, "t-001")

    response = _invoke(_ops_event("POST", "/v1/platform/ops/tenants/t-001/suspend", body={}))
    assert response["statusCode"] == 400
    assert "reason" in _body(response)["error"]["message"].lower()


def test_suspend_tenant_not_found(fake_state: dict[str, Any]) -> None:
    response = _invoke(
        _ops_event("POST", "/v1/platform/ops/tenants/t-missing/suspend", body={"reason": "test"})
    )
    assert response["statusCode"] == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"
    assert _event_count(fake_state) == 0


def test_suspend_tenant_already_suspended(fake_state: dict[str, Any]) -> None:
    _seed_tenant(fake_state, "t-001", status="suspended")

    response = _invoke(
        _ops_event("POST", "/v1/platform/ops/tenants/t-001/suspend", body={"reason": "again"})
    )
    assert response["statusCode"] == 409
    assert _body(response)["error"]["code"] == "ALREADY_SUSPENDED"
    assert _event_count(fake_state) == 0


def test_suspend_deleted_tenant(fake_state: dict[str, Any]) -> None:
    _seed_tenant(fake_state, "t-001", status="deleted")

    response = _invoke(
        _ops_event("POST", "/v1/platform/ops/tenants/t-001/suspend", body={"reason": "test"})
    )
    assert response["statusCode"] == 409
    assert _body(response)["error"]["code"] == "TENANT_DELETED"
    assert _event_count(fake_state) == 0


# ---------------------------------------------------------------------------
# Reinstate tenant — authorized flow
# ---------------------------------------------------------------------------


def test_reinstate_tenant_success(fake_state: dict[str, Any]) -> None:
    _seed_tenant(fake_state, "t-001", status="suspended")

    response = _invoke(
        _ops_event(
            "POST",
            "/v1/platform/ops/tenants/t-001/reinstate",
            body={"reason": "budget resolved"},
        )
    )
    assert response["statusCode"] == 200
    body = _body(response)
    assert body["tenantId"] == "t-001"
    assert body["status"] == "active"
    assert body["reason"] == "budget resolved"

    # Verify DB
    record = fake_state["db"].items[("TENANT#t-001", "METADATA")]
    assert record["status"] == "active"

    # Verify audit event
    # Verify response audit envelope
    assert body["audit"]["operationType"] == "tenant_reinstate"
    assert body["audit"]["targetTenantId"] == "t-001"

    # Verify EventBridge audit event
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.reinstated"
    assert detail["targetTenantId"] == "t-001"
    assert detail["operationType"] == "tenant_reinstate"
    assert detail["outcome"] == "success"


def test_reinstate_tenant_default_reason(fake_state: dict[str, Any]) -> None:
    _seed_tenant(fake_state, "t-001", status="suspended")

    response = _invoke(_ops_event("POST", "/v1/platform/ops/tenants/t-001/reinstate", body={}))
    assert response["statusCode"] == 200
    assert _body(response)["reason"] == "Reinstated by operator"


def test_reinstate_tenant_not_found(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("POST", "/v1/platform/ops/tenants/t-missing/reinstate", body={}))
    assert response["statusCode"] == 404
    assert _body(response)["error"]["code"] == "NOT_FOUND"
    assert _event_count(fake_state) == 0


def test_reinstate_tenant_not_suspended(fake_state: dict[str, Any]) -> None:
    _seed_tenant(fake_state, "t-001", status="active")

    response = _invoke(_ops_event("POST", "/v1/platform/ops/tenants/t-001/reinstate", body={}))
    assert response["statusCode"] == 409
    assert _body(response)["error"]["code"] == "NOT_SUSPENDED"
    assert _event_count(fake_state) == 0


# ---------------------------------------------------------------------------
# Suspend then reinstate — full lifecycle
# ---------------------------------------------------------------------------


def test_suspend_then_reinstate_lifecycle(fake_state: dict[str, Any]) -> None:
    _seed_tenant(fake_state, "t-lifecycle")

    # Suspend
    response = _invoke(
        _ops_event(
            "POST",
            "/v1/platform/ops/tenants/t-lifecycle/suspend",
            body={"reason": "maintenance"},
        )
    )
    assert response["statusCode"] == 200
    assert fake_state["db"].items[("TENANT#t-lifecycle", "METADATA")]["status"] == "suspended"

    # Reinstate
    response = _invoke(
        _ops_event(
            "POST",
            "/v1/platform/ops/tenants/t-lifecycle/reinstate",
            body={"reason": "maintenance complete"},
        )
    )
    assert response["statusCode"] == 200
    assert fake_state["db"].items[("TENANT#t-lifecycle", "METADATA")]["status"] == "active"

    # Two audit events emitted
    assert _event_count(fake_state) == 2


# ---------------------------------------------------------------------------
# Notify tenant — authorized flow
# ---------------------------------------------------------------------------


def test_notify_tenant_success(fake_state: dict[str, Any]) -> None:
    _seed_tenant(fake_state, "t-001")

    response = _invoke(
        _ops_event(
            "POST",
            "/v1/platform/ops/tenants/t-001/notify",
            body={"template": "maintenance-window"},
        )
    )
    assert response["statusCode"] == 200
    body = _body(response)
    assert body["status"] == "sent"
    assert body["template"] == "maintenance-window"

    assert body["audit"]["operationType"] == "tenant_notify"

    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.notification_sent"
    assert detail["targetTenantId"] == "t-001"
    assert detail["template"] == "maintenance-window"


def test_notify_tenant_missing_template(fake_state: dict[str, Any]) -> None:
    _seed_tenant(fake_state, "t-001")

    response = _invoke(_ops_event("POST", "/v1/platform/ops/tenants/t-001/notify", body={}))
    assert response["statusCode"] == 400
    assert "template" in _body(response)["error"]["message"].lower()


def test_notify_tenant_not_found(fake_state: dict[str, Any]) -> None:
    response = _invoke(
        _ops_event(
            "POST",
            "/v1/platform/ops/tenants/t-missing/notify",
            body={"template": "test"},
        )
    )
    assert response["statusCode"] == 404
    assert _event_count(fake_state) == 0


# ---------------------------------------------------------------------------
# Fail job — authorized flow
# ---------------------------------------------------------------------------


def test_fail_job_success(fake_state: dict[str, Any]) -> None:
    response = _invoke(
        _ops_event("POST", "/v1/platform/ops/jobs/job-123/fail", body={"reason": "manual fail"})
    )
    assert response["statusCode"] == 200
    body = _body(response)
    assert body["jobId"] == "job-123"
    assert body["status"] == "failed"

    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "job.failed_by_operator"
    assert detail["operationType"] == "job_fail"
    assert detail["jobId"] == "job-123"
    assert detail["reason"] == "manual fail"


def test_fail_job_missing_reason(fake_state: dict[str, Any]) -> None:
    response = _invoke(_ops_event("POST", "/v1/platform/ops/jobs/job-123/fail", body={}))
    assert response["statusCode"] == 400
    assert "reason" in _body(response)["error"]["message"].lower()


# ---------------------------------------------------------------------------
# RBAC — non-admin callers are rejected
# ---------------------------------------------------------------------------


def test_ops_routes_reject_non_admin(fake_state: dict[str, Any]) -> None:
    """All ops routes require Platform.Admin; a regular tenant caller is rejected."""
    _seed_tenant(fake_state, "t-001")

    for method, path, body in [
        ("POST", "/v1/platform/ops/tenants/t-001/suspend", {"reason": "test"}),
        ("POST", "/v1/platform/ops/tenants/t-001/reinstate", {}),
        ("POST", "/v1/platform/ops/tenants/t-001/notify", {"template": "test"}),
        ("POST", "/v1/platform/ops/jobs/job-1/fail", {"reason": "test"}),
        ("GET", "/v1/platform/ops/top-tenants", None),
    ]:
        event = _ops_event(method, path, body=body, roles="Agent.Invoke", tenant_id="t-001")
        response = _invoke(event)
        assert response["statusCode"] == 403, (
            f"Expected 403 for {method} {path}, got {response['statusCode']}"
        )

    # No audit events should have been emitted
    assert _event_count(fake_state) == 0


# ---------------------------------------------------------------------------
# Audit envelope completeness
# ---------------------------------------------------------------------------


def test_audit_envelope_fields(fake_state: dict[str, Any]) -> None:
    """Every tenant mutation audit event contains the required envelope fields."""
    _seed_tenant(fake_state, "t-audit")

    _invoke(
        _ops_event(
            "POST",
            "/v1/platform/ops/tenants/t-audit/suspend",
            body={"reason": "audit check"},
            sub="operator-42",
            tenant_id="platform",
        )
    )

    _, detail = _last_event_detail(fake_state)
    assert detail["schemaVersion"] == 1
    assert detail["actorTenantId"] == "platform"
    assert detail["actorSub"] == "operator-42"
    assert detail["targetTenantId"] == "t-audit"
    assert detail["operationType"] == "tenant_suspend"
    assert detail["outcome"] == "success"
    assert detail["reason"] == "audit check"
    assert "occurredAt" in detail
