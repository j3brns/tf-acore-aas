from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data_access.models import PaginatedItems

from src.tenant_api import handler as tenant_api_handler


class FakeScopedDb:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def get_item(self, _table_name: str, key: dict[str, Any]) -> dict[str, Any] | None:
        item = self.items.get((str(key["PK"]), str(key["SK"])))
        if item is None:
            return None
        return dict(item)

    def put_item(self, _table_name: str, item: dict[str, Any]) -> dict[str, Any]:
        pk = str(item["PK"])
        sk = str(item["SK"])
        self.items[(pk, sk)] = dict(item)
        return {"Attributes": dict(item)}

    def delete_item(self, _table_name: str, key: dict[str, Any]) -> dict[str, Any]:
        pk = str(key["PK"])
        sk = str(key["SK"])
        self.items.pop((pk, sk), None)
        return {}

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
        _table_name: str | None = None,
        *,
        filter_expression: Any | None = None,
        limit: int | None = None,
        exclusive_start_key: dict[str, Any] | None = None,
        expression_attribute_names: dict[str, str] | None = None,
        expression_attribute_values: dict[str, Any] | None = None,
    ) -> PaginatedItems:
        # Match real lib: _table_name is NOT None if we use it correctly
        results = [dict(item) for item in self.items.values()]
        # Simple filter implementation for tests
        status_filter = (expression_attribute_values or {}).get(":s")
        tier_filter = (expression_attribute_values or {}).get(":t")
        if status_filter:
            results = [r for r in results if r.get("status") == status_filter]
        if tier_filter:
            results = [r for r in results if r.get("tier") == tier_filter]

        # Paginated results simulation
        last_key = None
        if limit and len(results) > limit:
            # Not really accurate for DynamoDB but enough for tests
            last_key = {"PK": results[limit - 1]["PK"], "SK": results[limit - 1]["SK"]}
            results = results[:limit]

        return PaginatedItems(items=results, last_evaluated_key=last_key)

    def query(
        self,
        _table_name: str | None = None,
        *,
        sk_condition: Any | None = None,
        filter_expression: Any | None = None,
        index_name: str | None = None,
        limit: int | None = None,
        scan_index_forward: bool = True,
        exclusive_start_key: dict[str, Any] | None = None,
    ) -> PaginatedItems:
        # Mock query — return items matching SK prefix if provided
        results = [dict(item) for item in self.items.values()]
        if sk_condition:
            cls_name = type(sk_condition).__name__
            if cls_name == "BeginsWith" and hasattr(sk_condition, "_values"):
                prefix = sk_condition._values[1]
                results = [r for r in results if str(r.get("SK", "")).startswith(prefix)]
            elif cls_name == "Between" and hasattr(sk_condition, "_values"):
                v_min = sk_condition._values[1]
                v_max = sk_condition._values[2]
                results = [r for r in results if v_min <= str(r.get("SK", "")) <= v_max]
            else:
                # Fallback to string matching if _values not present or unknown type
                cond_str = str(sk_condition)
                if "INVITE#" in cond_str:
                    results = [r for r in results if str(r.get("SK", "")).startswith("INVITE#")]
                elif "WEBHOOK#" in cond_str:
                    results = [r for r in results if str(r.get("SK", "")).startswith("WEBHOOK#")]

        return PaginatedItems(items=results)


class FakeSecretsManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.rotate_calls: list[dict[str, Any]] = []

    def create_secret(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"ARN": f"arn:aws:secretsmanager:eu-west-2:111111111111:secret:{kwargs['Name']}"}

    def put_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        self.rotate_calls.append(kwargs)
        return {"ARN": str(kwargs.get("SecretId", "")), "VersionId": "ver-rotated-001"}


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


class FakeTenantScopedS3:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.presign_calls: list[dict[str, Any]] = []

    def put_object(self, bucket: str, key: str, body: bytes, **kwargs: Any) -> None:
        self.put_calls.append(
            {
                "bucket": bucket,
                "key": key,
                "body": body,
                "kwargs": dict(kwargs),
            }
        )

    def generate_presigned_url(
        self,
        bucket: str,
        key: str,
        *,
        expires_in: int = 3600,
        client_method: str = "get_object",
    ) -> str:
        self.presign_calls.append(
            {
                "bucket": bucket,
                "key": key,
                "expires_in": expires_in,
                "client_method": client_method,
            }
        )
        return f"https://example.com/download/{key}?expires={expires_in}"


class FakeDynamoDbTable:
    def __init__(self) -> None:
        self.scan_calls: list[dict[str, Any]] = []

    def scan(self, **kwargs: Any) -> dict[str, Any]:
        self.scan_calls.append(dict(kwargs))
        return {"Items": []}


class FakeDynamoDbResource:
    def __init__(self) -> None:
        self.tables: dict[str, FakeDynamoDbTable] = {}

    def Table(self, name: str) -> FakeDynamoDbTable:  # noqa: N802 - boto3 compatibility
        if name not in self.tables:
            self.tables[name] = FakeDynamoDbTable()
        return self.tables[name]


class FakeLambdaContext:
    function_name = "tenant-api"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:tenant-api"
    aws_request_id = "req-123"


class FakeSsm:
    def __init__(self) -> None:
        self.parameters = {"/platform/config/runtime-region": "eu-west-1"}
        self.get_calls: list[dict[str, Any]] = []
        self.put_calls: list[dict[str, Any]] = []
        self.put_error: Exception | None = None

    def get_parameter(self, *, Name: str) -> dict[str, Any]:  # noqa: N803 - boto3 compatibility
        self.get_calls.append({"Name": Name})
        return {"Parameter": {"Name": Name, "Value": self.parameters[Name]}}

    def put_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(dict(kwargs))
        if self.put_error is not None:
            raise self.put_error
        self.parameters[str(kwargs["Name"])] = str(kwargs["Value"])
        return {"Version": 1}


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
        usage_client=FakeUsageClient(),
        memory_provisioner=FakeMemoryProvisioner(),
    )
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    monkeypatch.setenv("TENANTS_TABLE_NAME", "platform-tenants")
    monkeypatch.setenv("INVOCATIONS_TABLE_NAME", "platform-invocations")
    monkeypatch.setenv("EVENT_BUS_NAME", "platform-bus")
    monkeypatch.setenv("AUDIT_EXPORT_BUCKET", "platform-audit-exports")
    monkeypatch.setenv("AUDIT_EXPORT_URL_EXPIRY_SECONDS", "1800")
    monkeypatch.setenv("TENANT_API_KEY_SECRET_PREFIX", "platform/tenants")
    monkeypatch.setenv("OPS_LOCKS_TABLE", "platform-ops-locks")
    monkeypatch.setenv("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")
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


def _seed_failover_lock(
    fake_state: dict[str, Any],
    *,
    lock_id: str = "lock-123",
    ttl: int | None = None,
    acquired_by: str = "ops@example.com",
) -> None:
    expires_at = ttl if ttl is not None else int(datetime.now(UTC).timestamp()) + 300
    fake_state["db"].items[("LOCK#platform-runtime-failover", "METADATA")] = {
        "PK": "LOCK#platform-runtime-failover",
        "SK": "METADATA",
        "lockId": lock_id,
        "acquiredBy": acquired_by,
        "ttl": expires_at,
    }


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


def test_create_tenant_normalizes_tenant_id_to_lowercase(fake_state: dict[str, Any]) -> None:
    response = _invoke(
        _event(
            method="POST",
            body={
                "tenantId": "Tenant-Acme-001",
                "appId": "app-001",
                "displayName": "Acme Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
            },
        )
    )

    assert response["statusCode"] == 201
    body = _body(response)
    assert body["tenant"]["tenantId"] == "tenant-acme-001"
    assert fake_state["deps"].memory_provisioner.calls == [
        {"tenant_id": "tenant-acme-001", "app_id": "app-001"}
    ]


@pytest.mark.parametrize(
    ("tenant_id", "expected_error"),
    [
        ("ab", "tenantId must be 3-32 characters"),
        ("a" * 33, "tenantId must be 3-32 characters"),
        ("tenant--one", "tenantId must not contain consecutive hyphens"),
        ("tenant_one", "tenantId must match ^[a-z](?:[a-z0-9-]{1,30}[a-z0-9])$"),
        ("stub", "tenantId is reserved"),
    ],
)
def test_create_tenant_rejects_invalid_tenant_id_values(
    fake_state: dict[str, Any], tenant_id: str, expected_error: str
) -> None:
    response = _invoke(
        _event(
            method="POST",
            body={
                "tenantId": tenant_id,
                "appId": "app-001",
                "displayName": "Acme Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
            },
        )
    )

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == expected_error


def test_create_tenant_detects_collision_after_tenant_id_normalization(
    fake_state: dict[str, Any],
) -> None:
    first = _invoke(
        _event(
            method="POST",
            body={
                "tenantId": "tenant-collision-001",
                "appId": "app-001",
                "displayName": "Acme Ltd",
                "tier": "standard",
                "ownerEmail": "owner@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
            },
        )
    )
    assert first["statusCode"] == 201

    second = _invoke(
        _event(
            method="POST",
            body={
                "tenantId": "TENANT-COLLISION-001",
                "appId": "app-001",
                "displayName": "Acme Ltd 2",
                "tier": "standard",
                "ownerEmail": "owner2@example.com",
                "ownerTeam": "team-acme",
                "accountId": "123456789012",
            },
        )
    )

    assert second["statusCode"] == 409
    error = _body(second)["error"]
    assert error["code"] == "CONFLICT"
    assert error["message"] == "Tenant already exists"


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


def test_read_own_tenant_canonicalizes_mixed_case_path_tenant_id(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#tenant-acme-001", "METADATA")] = {
        "PK": "TENANT#tenant-acme-001",
        "SK": "METADATA",
        "tenantId": "tenant-acme-001",
        "appId": "app-002",
        "displayName": "Bravo",
        "tier": "basic",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "b@example.com",
        "ownerTeam": "team-b",
        "accountId": "123456789012",
    }

    response = _invoke(
        _event(
            method="GET",
            tenant_id="Tenant-Acme-001",
            caller_tenant_id="tenant-acme-001",
            roles=[],
            app_id="app-002",
        )
    )

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "tenant-acme-001"


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
@pytest.mark.parametrize("tenant_id", ["ab", "tenant--one", "tenant_one", "stub"])
def test_path_based_tenant_routes_reject_invalid_tenant_ids_deterministically(
    fake_state: dict[str, Any],
    method: str,
    tenant_id: str,
) -> None:
    body = {"tier": "premium"} if method == "PATCH" else None

    response = _invoke(_event(method=method, tenant_id=tenant_id, body=body))

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"].startswith("tenantId ")


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


def test_update_canonicalizes_mixed_case_path_tenant_id(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#tenant-acme-002", "METADATA")] = {
        "PK": "TENANT#tenant-acme-002",
        "SK": "METADATA",
        "tenantId": "tenant-acme-002",
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

    response = _invoke(
        _event(method="PATCH", tenant_id="TENANT-ACME-002", body={"tier": "premium"})
    )

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "tenant-acme-002"
    assert tenant["tier"] == "premium"


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


def test_delete_canonicalizes_mixed_case_path_tenant_id(
    fake_state: dict[str, Any],
    fixed_now: datetime,
) -> None:
    fake_state["db"].items[("TENANT#tenant-acme-003", "METADATA")] = {
        "PK": "TENANT#tenant-acme-003",
        "SK": "METADATA",
        "tenantId": "tenant-acme-003",
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

    response = _invoke(_event(method="DELETE", tenant_id="Tenant-Acme-003"))

    assert response["statusCode"] == 200
    tenant = _body(response)["tenant"]
    assert tenant["tenantId"] == "tenant-acme-003"
    assert tenant["status"] == "deleted"
    assert tenant["purgeAtEpochSeconds"] == int((fixed_now + timedelta(days=30)).timestamp())


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


def test_audit_export_writes_real_s3_export_and_returns_presigned_url(
    fake_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_state["db"].items[("TENANT#t-005", "METADATA")] = {
        "PK": "TENANT#t-005",
        "SK": "METADATA",
        "tenantId": "t-005",
        "status": "active",
        "appId": "app-005",
    }
    fake_state["db"].items[("TENANT#t-005", "INV#2026-02-25T10:00:00Z#inv-001")] = {
        "PK": "TENANT#t-005",
        "SK": "INV#2026-02-25T10:00:00Z#inv-001",
        "tenantId": "t-005",
        "appId": "app-005",
        "invocationId": "inv-001",
        "timestamp": "2026-02-25T10:00:00Z",
        "status": "success",
    }
    fake_state["db"].items[("TENANT#t-005", "INV#2026-02-25T13:00:00Z#inv-002")] = {
        "PK": "TENANT#t-005",
        "SK": "INV#2026-02-25T13:00:00Z#inv-002",
        "tenantId": "t-005",
        "appId": "app-005",
        "invocationId": "inv-002",
        "timestamp": "2026-02-25T13:00:00Z",
        "status": "success",
    }

    fake_s3 = FakeTenantScopedS3()
    monkeypatch.setattr(tenant_api_handler.secrets, "token_hex", lambda _n: "feedfacecafebeef")
    monkeypatch.setattr(tenant_api_handler, "_tenant_s3_for_scope", lambda **_kwargs: fake_s3)

    event = _event(method="GET", tenant_id="t-005")
    event["path"] = "/v1/tenants/t-005/audit-export"
    event["queryStringParameters"] = {
        "start": "2026-02-25T09:00:00Z",
        "end": "2026-02-25T11:00:00Z",
    }
    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["tenantId"] == "t-005"
    assert body["downloadUrl"].startswith("https://example.com/download/")
    assert body["expiresAt"] == "2026-02-25T12:30:00Z"

    assert len(fake_s3.put_calls) == 1
    put_call = fake_s3.put_calls[0]
    assert put_call["bucket"] == "platform-audit-exports"
    assert (
        put_call["key"]
        == "tenants/t-005/audit-exports/audit-export-20260225T120000Z-feedfacecafebeef.json"
    )
    assert put_call["kwargs"]["ContentType"] == "application/json"

    exported_payload = json.loads(put_call["body"].decode("utf-8"))
    assert exported_payload["tenantId"] == "t-005"
    assert exported_payload["recordCount"] == 1
    assert exported_payload["windowStart"] == "2026-02-25T09:00:00Z"
    assert exported_payload["windowEnd"] == "2026-02-25T11:00:00Z"
    assert exported_payload["records"][0]["invocationId"] == "inv-001"

    assert fake_s3.presign_calls == [
        {
            "bucket": "platform-audit-exports",
            "key": (
                "tenants/t-005/audit-exports/audit-export-20260225T120000Z-feedfacecafebeef.json"
            ),
            "expires_in": 1800,
            "client_method": "get_object",
        }
    ]


def test_audit_export_rejects_invalid_time_window(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-005", "METADATA")] = {
        "PK": "TENANT#t-005",
        "SK": "METADATA",
        "tenantId": "t-005",
        "status": "active",
        "appId": "app-005",
    }

    event = _event(method="GET", tenant_id="t-005")
    event["path"] = "/v1/tenants/t-005/audit-export"
    event["queryStringParameters"] = {
        "start": "2026-02-25T12:00:00Z",
        "end": "2026-02-25T11:00:00Z",
    }

    response = _invoke(event)

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"] == "start must be less than or equal to end"


def test_audit_export_requires_bucket_configuration(
    fake_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_state["db"].items[("TENANT#t-005", "METADATA")] = {
        "PK": "TENANT#t-005",
        "SK": "METADATA",
        "tenantId": "t-005",
        "status": "active",
        "appId": "app-005",
    }
    monkeypatch.delenv("AUDIT_EXPORT_BUCKET")

    event = _event(method="GET", tenant_id="t-005")
    event["path"] = "/v1/tenants/t-005/audit-export"

    response = _invoke(event)

    assert response["statusCode"] == 500
    error = _body(response)["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert error["message"] == "Audit export bucket is not configured"


def test_platform_failover_updates_runtime_region_when_lock_is_valid(
    fake_state: dict[str, Any], fixed_now: datetime
) -> None:
    _seed_failover_lock(fake_state, ttl=int(fixed_now.timestamp()) + 300)
    event = _event(method="POST", body={"targetRegion": "eu-central-1", "lockId": "lock-123"})
    event["path"] = "/v1/platform/failover"
    response = _invoke(event)

    assert response["statusCode"] == 200
    assert _body(response) == {
        "status": "completed",
        "region": "eu-central-1",
        "previousRegion": "eu-west-1",
        "lockId": "lock-123",
        "changed": True,
    }
    assert fake_state["deps"].ssm.parameters["/platform/config/runtime-region"] == "eu-central-1"
    assert fake_state["deps"].ssm.put_calls == [
        {
            "Name": "/platform/config/runtime-region",
            "Value": "eu-central-1",
            "Type": "String",
            "Overwrite": True,
        }
    ]


def test_platform_failover_is_idempotent_when_target_region_is_already_active(
    fake_state: dict[str, Any], fixed_now: datetime
) -> None:
    _seed_failover_lock(fake_state, ttl=int(fixed_now.timestamp()) + 300)
    fake_state["deps"].ssm.parameters["/platform/config/runtime-region"] = "eu-central-1"
    event = _event(method="POST", body={"targetRegion": "eu-central-1", "lockId": "lock-123"})
    event["path"] = "/v1/platform/failover"

    response = _invoke(event)

    assert response["statusCode"] == 200
    assert _body(response) == {
        "status": "completed",
        "region": "eu-central-1",
        "previousRegion": "eu-central-1",
        "lockId": "lock-123",
        "changed": False,
    }
    assert fake_state["deps"].ssm.put_calls == []


def test_platform_failover_rejects_missing_lock(fake_state: dict[str, Any]) -> None:
    event = _event(method="POST", body={"targetRegion": "eu-central-1", "lockId": "lock-123"})
    event["path"] = "/v1/platform/failover"

    response = _invoke(event)

    assert response["statusCode"] == 409
    assert _body(response)["error"]["code"] == "LOCK_NOT_HELD"
    assert fake_state["deps"].ssm.put_calls == []


def test_platform_failover_rejects_expired_lock(
    fake_state: dict[str, Any], fixed_now: datetime
) -> None:
    _seed_failover_lock(fake_state, ttl=int(fixed_now.timestamp()) - 1)
    event = _event(method="POST", body={"targetRegion": "eu-central-1", "lockId": "lock-123"})
    event["path"] = "/v1/platform/failover"

    response = _invoke(event)

    assert response["statusCode"] == 409
    assert _body(response)["error"]["code"] == "LOCK_EXPIRED"
    assert fake_state["deps"].ssm.put_calls == []


def test_platform_failover_rejects_lock_owned_by_another_actor(
    fake_state: dict[str, Any], fixed_now: datetime
) -> None:
    _seed_failover_lock(fake_state, lock_id="other-lock", ttl=int(fixed_now.timestamp()) + 300)
    event = _event(method="POST", body={"targetRegion": "eu-central-1", "lockId": "lock-123"})
    event["path"] = "/v1/platform/failover"

    response = _invoke(event)

    assert response["statusCode"] == 409
    assert _body(response)["error"]["code"] == "LOCK_MISMATCH"
    assert fake_state["deps"].ssm.put_calls == []


def test_platform_failover_ssm_update_failure_returns_error_and_logs_context(
    fake_state: dict[str, Any], fixed_now: datetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_failover_lock(fake_state, ttl=int(fixed_now.timestamp()) + 300)
    fake_state["deps"].ssm.put_error = tenant_api_handler.ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "PutParameter",
    )
    logged: list[tuple[str, dict[str, Any]]] = []

    def _capture_exception(message: str, *args: Any, **kwargs: Any) -> None:
        extra = kwargs.get("extra")
        if message == "Platform failover SSM update failed" and isinstance(extra, dict):
            logged.append((message, dict(extra)))

    monkeypatch.setattr(tenant_api_handler.logger, "exception", _capture_exception)
    event = _event(method="POST", body={"targetRegion": "eu-central-1", "lockId": "lock-123"})
    event["path"] = "/v1/platform/failover"

    response = _invoke(event)

    assert response["statusCode"] == 502
    assert _body(response)["error"]["code"] == "AWS_CLIENT_ERROR"
    assert fake_state["deps"].ssm.parameters["/platform/config/runtime-region"] == "eu-west-1"
    assert logged == [
        (
            "Platform failover SSM update failed",
            {
                "actor": "user-123",
                "lock_id": "lock-123",
                "lock_owner": "ops@example.com",
                "previous_region": "eu-west-1",
                "target_region": "eu-central-1",
            },
        )
    ]


def test_platform_failover_requires_platform_admin_role(fake_state: dict[str, Any]) -> None:
    event = _event(method="POST", body={"targetRegion": "eu-central-1", "lockId": "lock-123"})
    event["path"] = "/v1/platform/failover"

    # Non-admin forbidden
    event_non_admin = _event(method="POST", roles=[], body={"targetRegion": "x", "lockId": "y"})
    event_non_admin["path"] = "/v1/platform/failover"
    response = _invoke(event_non_admin)
    assert response["statusCode"] == 403


def test_health_route_returns_openapi_shape(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET")
    event["path"] = "/v1/health"
    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["status"] == "ok"
    assert "version" in body
    assert "timestamp" in body


def test_sessions_route_returns_items_list(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET", roles=[], caller_tenant_id="t-001", app_id="app-001")
    event["path"] = "/v1/sessions"
    event["queryStringParameters"] = {"limit": "5"}
    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body == {"items": []}


def test_sessions_route_rejects_invalid_limit(fake_state: dict[str, Any]) -> None:
    event = _event(method="GET", roles=[], caller_tenant_id="t-001", app_id="app-001")
    event["path"] = "/v1/sessions"
    event["queryStringParameters"] = {"limit": "abc"}
    response = _invoke(event)

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"


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


def test_parse_roles_accepts_json_encoded_array() -> None:
    parsed = tenant_api_handler._parse_roles('["Platform.Admin","Platform.Operator"]')
    assert parsed == frozenset({"Platform.Admin", "Platform.Operator"})


def test_create_tenant_allows_json_encoded_admin_roles(fake_state: dict[str, Any]) -> None:
    response = _invoke(
        _event(
            method="POST",
            tenant_id=None,
            roles='["Platform.Admin"]',
            body={
                "tenantId": "t-json-001",
                "appId": "app-json-001",
                "displayName": "Json Role Tenant",
                "tier": "basic",
                "ownerEmail": "json@example.com",
                "ownerTeam": "team-json",
                "accountId": "123456789012",
            },
        )
    )

    assert response["statusCode"] == 201


def test_rotate_api_key_for_own_tenant_succeeds_and_emits_event(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-rotate", "METADATA")] = {
        "PK": "TENANT#t-rotate",
        "SK": "METADATA",
        "tenantId": "t-rotate",
        "appId": "app-rotate",
        "displayName": "Rotate",
        "tier": "standard",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "r@example.com",
        "ownerTeam": "team-r",
        "accountId": "123456789012",
        "apiKeySecretArn": (
            "arn:aws:secretsmanager:eu-west-2:111111111111:secret:platform/tenants/t-rotate/api-key"
        ),
    }
    event = _event(
        method="POST",
        tenant_id="t-rotate",
        caller_tenant_id="t-rotate",
        roles=[],
        app_id="app-rotate",
    )
    event["path"] = "/v1/tenants/t-rotate/api-key/rotate"

    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["tenantId"] == "t-rotate"
    assert body["versionId"] == "ver-rotated-001"
    rotate_calls = fake_state["deps"].secretsmanager.rotate_calls
    assert len(rotate_calls) == 1
    assert rotate_calls[0]["SecretId"].endswith("/t-rotate/api-key")
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.api_key_rotated"
    assert detail["tenantId"] == "t-rotate"


def test_rotate_api_key_canonicalizes_mixed_case_path_tenant_id(
    fake_state: dict[str, Any],
) -> None:
    fake_state["db"].items[("TENANT#tenant-rotate-001", "METADATA")] = {
        "PK": "TENANT#tenant-rotate-001",
        "SK": "METADATA",
        "tenantId": "tenant-rotate-001",
        "appId": "app-rotate",
        "displayName": "Rotate",
        "tier": "standard",
        "status": "active",
        "createdAt": "2026-02-25T12:00:00Z",
        "updatedAt": "2026-02-25T12:00:00Z",
        "ownerEmail": "r@example.com",
        "ownerTeam": "team-r",
        "accountId": "123456789012",
        "apiKeySecretArn": (
            "arn:aws:secretsmanager:eu-west-2:111111111111:secret:"
            "platform/tenants/tenant-rotate-001/api-key"
        ),
    }
    event = _event(
        method="POST",
        tenant_id="Tenant-Rotate-001",
        caller_tenant_id="tenant-rotate-001",
        roles=[],
        app_id="app-rotate",
    )
    event["path"] = "/v1/tenants/Tenant-Rotate-001/api-key/rotate"

    response = _invoke(event)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["tenantId"] == "tenant-rotate-001"
    rotate_calls = fake_state["deps"].secretsmanager.rotate_calls
    assert rotate_calls[0]["SecretId"].endswith("/tenant-rotate-001/api-key")


def test_rotate_api_key_cross_tenant_forbidden(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-owner", "METADATA")] = {
        "PK": "TENANT#t-owner",
        "SK": "METADATA",
        "tenantId": "t-owner",
        "appId": "app-owner",
        "status": "active",
        "apiKeySecretArn": (
            "arn:aws:secretsmanager:eu-west-2:111111111111:secret:platform/tenants/t-owner/api-key"
        ),
    }
    event = _event(
        method="POST",
        tenant_id="t-owner",
        caller_tenant_id="t-attacker",
        roles=[],
        app_id="app-attacker",
    )
    event["path"] = "/v1/tenants/t-owner/api-key/rotate"

    response = _invoke(event)

    assert response["statusCode"] == 403
    assert _body(response)["error"]["code"] == "FORBIDDEN"


def test_invite_user_for_own_tenant_succeeds(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-invite", "METADATA")] = {
        "PK": "TENANT#t-invite",
        "SK": "METADATA",
        "tenantId": "t-invite",
        "appId": "app-invite",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="t-invite",
        caller_tenant_id="t-invite",
        roles=[],
        app_id="app-invite",
        body={"email": "new.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/t-invite/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 202
    invite = _body(response)["invite"]
    assert invite["tenantId"] == "t-invite"
    assert invite["email"] == "new.user@example.com"
    assert invite["status"] == "pending"
    detail_type, detail = _last_event_detail(fake_state)
    assert detail_type == "tenant.user_invited"
    assert detail["tenantId"] == "t-invite"


def test_invite_user_canonicalizes_mixed_case_path_tenant_id(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#tenant-invite-001", "METADATA")] = {
        "PK": "TENANT#tenant-invite-001",
        "SK": "METADATA",
        "tenantId": "tenant-invite-001",
        "appId": "app-invite",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="Tenant-Invite-001",
        caller_tenant_id="tenant-invite-001",
        roles=[],
        app_id="app-invite",
        body={"email": "new.user@example.com", "role": "Agent.Invoke"},
    )
    event["path"] = "/v1/tenants/Tenant-Invite-001/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 202
    invite = _body(response)["invite"]
    assert invite["tenantId"] == "tenant-invite-001"


@pytest.mark.parametrize(
    ("path", "tenant_id"),
    [
        ("/v1/tenants/stub/api-key/rotate", "stub"),
        ("/v1/tenants/tenant_one/users/invite", "tenant_one"),
        ("/v1/tenants/tenant--one/audit-export", "tenant--one"),
    ],
)
def test_tenant_subroutes_reject_invalid_path_tenant_ids_before_route_logic(
    fake_state: dict[str, Any],
    path: str,
    tenant_id: str,
) -> None:
    event = _event(
        method="POST" if path.endswith(("rotate", "invite")) else "GET",
        tenant_id=tenant_id,
        caller_tenant_id="tenant-owner-001",
        roles=["Platform.Admin"],
        body={"email": "new.user@example.com"} if path.endswith("invite") else None,
    )
    event["path"] = path

    response = _invoke(event)

    assert response["statusCode"] == 400
    error = _body(response)["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["message"].startswith("tenantId ")


def test_invite_user_requires_valid_email(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-invite-2", "METADATA")] = {
        "PK": "TENANT#t-invite-2",
        "SK": "METADATA",
        "tenantId": "t-invite-2",
        "appId": "app-invite-2",
        "status": "active",
    }
    event = _event(
        method="POST",
        tenant_id="t-invite-2",
        caller_tenant_id="t-invite-2",
        roles=[],
        app_id="app-invite-2",
        body={"email": "not-an-email"},
    )
    event["path"] = "/v1/tenants/t-invite-2/users/invite"

    response = _invoke(event)

    assert response["statusCode"] == 400
    assert _body(response)["error"]["code"] == "BAD_REQUEST"


def test_webhook_management_succeeds(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-webhook", "METADATA")] = {
        "PK": "TENANT#t-webhook",
        "SK": "METADATA",
        "tenantId": "t-webhook",
        "appId": "app-webhook",
        "status": "active",
    }

    # 1. Register a webhook
    event = _event(
        method="POST",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Agent.Invoke"],
        body={
            "callbackUrl": "https://example.com/callback",
            "events": ["job.completed"],
            "description": "My Webhook",
        },
    )
    event["path"] = "/v1/webhooks"

    response = _invoke(event)
    assert response["statusCode"] == 201
    body = _body(response)
    webhook_id = body["webhookId"]
    assert body["callbackUrl"] == "https://example.com/callback"
    assert body["status"] == "active"

    # 2. List webhooks
    event = _event(
        method="GET",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Agent.Invoke"],
    )
    event["path"] = "/v1/webhooks"

    response = _invoke(event)
    assert response["statusCode"] == 200
    body = _body(response)
    assert len(body["items"]) == 1
    assert body["items"][0]["webhookId"] == webhook_id

    # 3. Delete webhook
    event = _event(
        method="DELETE",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Agent.Invoke"],
    )
    event["path"] = f"/v1/webhooks/{webhook_id}"

    response = _invoke(event)
    assert response["statusCode"] == 204

    # 4. Verify deleted
    event = _event(
        method="GET",
        tenant_id="t-webhook",
        caller_tenant_id="t-webhook",
        roles=["Agent.Invoke"],
    )
    event["path"] = "/v1/webhooks"

    response = _invoke(event)
    body = _body(response)
    assert len(body["items"]) == 0


def test_list_invites_succeeds(fake_state: dict[str, Any]) -> None:
    fake_state["db"].items[("TENANT#t-list-invites", "METADATA")] = {
        "PK": "TENANT#t-list-invites",
        "SK": "METADATA",
        "tenantId": "t-list-invites",
        "appId": "app-list-invites",
        "status": "active",
    }
    fake_state["db"].items[("TENANT#t-list-invites", "INVITE#inv-1")] = {
        "PK": "TENANT#t-list-invites",
        "SK": "INVITE#inv-1",
        "inviteId": "inv-1",
        "email": "user1@example.com",
        "status": "pending",
    }

    event = _event(
        method="GET",
        tenant_id="t-list-invites",
        caller_tenant_id="t-list-invites",
        roles=["Agent.Invoke"],
    )
    event["path"] = "/v1/tenants/t-list-invites/users/invites"

    response = _invoke(event)
    assert response["statusCode"] == 200
    body = _body(response)
    assert len(body["items"]) == 1
    assert body["items"][0]["inviteId"] == "inv-1"
