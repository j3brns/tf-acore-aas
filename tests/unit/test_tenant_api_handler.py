from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import handler as tenant_api_handler


class FakeScopedDb:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def get_item(self, _table_name: str, key: dict[str, Any]) -> dict[str, Any] | None:
        item = self.items.get((str(key["PK"]), str(key["SK"])))
        if item is None:
            return None
        return dict(item)

    def update_item(
        self,
        _table_name: str,
        key: dict[str, Any],
        update_expression: str,
        expression_attribute_values: dict[str, Any],
        *,
        expression_attribute_names: dict[str, str] | None = None,
        condition_expression: str | None = None,
    ) -> dict[str, Any]:
        pk = str(key["PK"])
        sk = str(key["SK"])
        storage_key = (pk, sk)
        existing = self.items.get(storage_key)

        if (
            condition_expression
            and "attribute_not_exists" in condition_expression
            and existing is not None
        ):
            raise tenant_api_handler.ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "UpdateItem",
            )
        if condition_expression and "attribute_exists" in condition_expression and existing is None:
            raise tenant_api_handler.ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "missing"}},
                "UpdateItem",
            )

        names = expression_attribute_names or {}
        item = dict(existing or {"PK": pk, "SK": sk})
        assert update_expression.startswith("SET ")
        for part in update_expression.removeprefix("SET ").split(", "):
            name_ref, value_ref = [token.strip() for token in part.split("=", 1)]
            attr_name = names.get(name_ref, name_ref.lstrip("#"))
            item[attr_name] = expression_attribute_values[value_ref]
        self.items[storage_key] = item
        return {"Attributes": dict(item)}

    def scan(
        self,
        _table_name: str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        results = [dict(item) for item in self.items.values()]
        # Simple filter implementation for tests
        status_filter = kwargs.get("ExpressionAttributeValues", {}).get(":s")
        tier_filter = kwargs.get("ExpressionAttributeValues", {}).get(":t")
        if status_filter:
            results = [r for r in results if r.get("status") == status_filter]
        if tier_filter:
            results = [r for r in results if r.get("tier") == tier_filter]
        return results


class FakeSecretsManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_secret(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"ARN": f"arn:aws:secretsmanager:eu-west-2:111111111111:secret:{kwargs['Name']}"}


class FakeEvents:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def put_events(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "evt-1"}]}


class FakeUsageClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_tenant_usage(self, *, tenant_id: str, app_id: str | None) -> dict[str, Any]:
        self.calls.append({"tenant_id": tenant_id, "app_id": app_id})
        return {"requestsToday": 12, "budgetRemainingUsd": 34.5}


class FakeMemoryProvisioner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def provision(self, *, tenant_id: str, app_id: str) -> dict[str, Any]:
        self.calls.append({"tenant_id": tenant_id, "app_id": app_id})
        return {"memoryStoreArn": f"arn:aws:memory:eu-west-2::store/{tenant_id}"}


class FakeLambdaContext:
    function_name = "tenant-api"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:tenant-api"
    aws_request_id = "req-123"


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


def _event(
    *,
    method: str,
    tenant_id: str | None = None,
    body: dict[str, Any] | None = None,
    caller_tenant_id: str | None = "t-admin",
    roles: str | list[str] = "Platform.Admin",
    app_id: str = "app-admin",
    usage_identifier_key: str | None = None,
) -> dict[str, Any]:
    path_params = None
    if tenant_id is not None:
        path_params = {"tenantId": tenant_id}
    authorizer: dict[str, Any] = {
        "tenantid": caller_tenant_id,
        "appid": app_id,
        "tier": "premium",
        "sub": "user-123",
        "roles": roles,
    }
    if usage_identifier_key is not None:
        authorizer["usageIdentifierKey"] = usage_identifier_key

    path = "/v1/tenants"
    if tenant_id is not None:
        path = f"/v1/tenants/{tenant_id}"

    return {
        "httpMethod": method,
        "path": path,
        "pathParameters": path_params,
        "body": None if body is None else json.dumps(body),
        "requestContext": {"authorizer": authorizer},
    }


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


def _invoke(event: dict[str, Any]) -> dict[str, Any]:
    return tenant_api_handler.lambda_handler(event, FakeLambdaContext())


def _last_event_detail(fake_state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    calls = fake_state["deps"].events.calls
    assert calls, "expected EventBridge put_events call"
    entry = calls[-1]["Entries"][0]
    return entry["DetailType"], json.loads(entry["Detail"])


def test_create_tenant_writes_record_provisions_memory_secret_and_emits_event(
    fake_state: dict[str, Any],
) -> None:
    response = _invoke(
        _event(
            method="POST",
            tenant_id=None,
            body={
                "tenantId": "t-001",
                "appId": "app-001",
                "displayName": "Acme Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
                "monthlyBudgetUsd": 99.5,
            },
        )
    )

    assert response["statusCode"] == 201
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "t-001"
    assert tenant["tier"] == "standard"
    assert tenant["apiKeySecretArn"].startswith("arn:aws:secretsmanager:")
    assert tenant["memoryStoreArn"].endswith("/t-001")
    assert fake_state["deps"].memory_provisioner.calls == [
        {"tenant_id": "t-001", "app_id": "app-001"}
    ]
    assert len(fake_state["deps"].secretsmanager.calls) == 1
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.created"
    assert detail["tenantId"] == "t-001"


def test_read_own_tenant_allowed_and_enriched_with_usage(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-002", "METADATA")] = {
        "PK": "TENANT#t-002",
        "SK": "METADATA",
        "tenantId": "t-002",
        "appId": "app-002",
        "displayName": "Bravo",
        "tier": "basic",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "b@example.com",
        "ownerTeam": "team-b",
        "accountId": "123456789012",
        "monthlyBudgetUsd": Decimal("50"),
    }

    response = _invoke(
        _event(
            method="GET",
            tenant_id="t-002",
            caller_tenant_id="t-002",
            roles=[],
            app_id="app-002",
            usage_identifier_key="usage-key-1",
        )
    )

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "t-002"
    assert tenant["usage"]["requestsToday"] == 12
    assert tenant["usage"]["usageIdentifierKey"] == "usage-key-1"


def test_read_other_tenant_forbidden_for_non_admin(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-victim", "METADATA")] = {
        "PK": "TENANT#t-victim",
        "SK": "METADATA",
        "tenantId": "t-victim",
        "appId": "app-victim",
        "displayName": "Victim",
        "tier": "standard",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "v@example.com",
        "ownerTeam": "team-v",
        "accountId": "123456789012",
    }

    response = _invoke(
        _event(
            method="GET",
            tenant_id="t-victim",
            caller_tenant_id="t-attacker",
            roles=[],
            app_id="app-attacker",
        )
    )

    assert response["statusCode"] == 403
    error = _body(response)["error"]
    assert error["code"] == "FORBIDDEN"


def test_update_tier_admin_only_emits_tier_changed_event(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-003", "METADATA")] = {
        "PK": "TENANT#t-003",
        "SK": "METADATA",
        "tenantId": "t-003",
        "appId": "app-003",
        "displayName": "Charlie",
        "tier": "basic",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "c@example.com",
        "ownerTeam": "team-c",
        "accountId": "123456789012",
    }

    response = _invoke(_event(method="PATCH", tenant_id="t-003", body={"tier": "premium"}))

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tier"] == "premium"
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.tier_changed"
    assert detail["oldTier"] == "basic"
    assert detail["newTier"] == "premium"


def test_delete_is_soft_delete_with_30_day_retention_and_event(
    fake_state: dict[str, Any],
    fixed_now: datetime,
) -> None:
    fake_state["db"].items[("TENANT#t-004", "METADATA")] = {
        "PK": "TENANT#t-004",
        "SK": "METADATA",
        "tenantId": "t-004",
        "appId": "app-004",
        "displayName": "Delta",
        "tier": "standard",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "d@example.com",
        "ownerTeam": "team-d",
        "accountId": "123456789012",
    }

    response = _invoke(_event(method="DELETE", tenant_id="t-004"))

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["status"] == "deleted"
    expected_purge = int((fixed_now + timedelta(days=30)).timestamp())
    assert tenant["purgeAtEpochSeconds"] == expected_purge
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.deleted"
    assert detail["retentionDays"] == 30
    assert detail["purgeAtEpochSeconds"] == expected_purge


def test_list_tenants_admin_only(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-1", "METADATA")] = {
        "PK": "TENANT#t-1",
        "SK": "METADATA",
        "tenantId": "t-1",
        "status": "active",
        "tier": "basic",
    }
    fake_state["db"].items[("TENANT#t-2", "METADATA")] = {
        "PK": "TENANT#t-2",
        "SK": "METADATA",
        "tenantId": "t-2",
        "status": "active",
        "tier": "premium",
    }

    # 1. Admin list all
    response = _invoke(_event(method="GET", tenant_id=None))
    assert response["statusCode"] == 200
    items = _body(response)["items"]
    assert len(items) == 2

    # 2. Non-admin only sees own
    response = _invoke(
        _event(method="GET", tenant_id=None, caller_tenant_id="t-1", roles=[], app_id="app-1")
    )
    assert response["statusCode"] == 200
    items = _body(response)["items"]
    assert len(items) == 1
    assert items[0]["tenantId"] == "t-1"


def test_audit_export_returns_presigned_url_stub(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-005", "METADATA")] = {
        "PK": "TENANT#t-005",
        "SK": "METADATA",
        "tenantId": "t-005",
        "status": "active",
    }

    event = _event(method="GET", tenant_id="t-005")
    event["path"] = "/v1/tenants/t-005/audit-export"
    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert "downloadUrl" in body
    assert body["tenantId"] == "t-005"


def test_platform_failover_requires_lock_and_admin(fake_state: dict[str, Any]) -> None:
    event = _event(method="POST", body={"targetRegion": "eu-central-1", "lockId": "lock-123"})
    event["path"] = "/v1/platform/failover"
    response = _invoke(event)

    assert response["statusCode"] == 200
    assert _body(response)["status"] == "initiated"

    # Non-admin forbidden
    event_non_admin = _event(method="POST", roles=[], body={"targetRegion": "x", "lockId": "y"})
    event_non_admin["path"] = "/v1/platform/failover"
    response = _invoke(event_non_admin)
    assert response["statusCode"] == 403


def test_platform_quota_report(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET")
    event["path"] = "/v1/platform/quota"
    response = _invoke(event)

    assert response["statusCode"] == 200
    utilisation = _body(response)["utilisation"]
    assert len(utilisation) > 0
    assert utilisation[0]["region"] == "eu-west-1"


def test_platform_split_accounts_requires_platform_admin(fake_state: dict[str, Any]) -> None:
    event = _event(method="POST", body={"tier": "premium", "targetAccountId": "123456789012"})
    event["path"] = "/v1/platform/quota/split-accounts"

    # 1. Platform.Admin succeeds
    response = _invoke(event)
    assert response["statusCode"] == 202
    assert "jobId" in _body(response)

    # 2. Platform.Operator (regular admin role in our _event helper) fails
    event_operator = _event(
        method="POST",
        roles=["Platform.Operator"],
        body={"tier": "premium", "targetAccountId": "123456789012"},
    )
    event_operator["path"] = "/v1/platform/quota/split-accounts"
    response = _invoke(event_operator)
    assert response["statusCode"] == 403
