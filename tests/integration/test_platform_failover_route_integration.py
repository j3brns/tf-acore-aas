from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import boto3
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "data-access-lib" / "src"))

bridge_handler = importlib.import_module("src.bridge.handler")
tenant_api_handler = importlib.import_module("src.tenant_api.handler")


def _load_failover_lock_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "failover_lock_script_integration", repo_root / "scripts" / "failover_lock.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


failover_lock = _load_failover_lock_module()


class FakeLambdaContext:
    function_name = "tenant-api"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:eu-west-2:111111111111:function:tenant-api"
    aws_request_id = "req-platform-failover-integration"


class _FakeEvents:
    def put_events(self, **kwargs: Any) -> dict[str, Any]:
        return {"FailedEntryCount": 0, "Entries": [{"EventId": "evt-1"}]}


class _FakeSecretsManager:
    pass


class _FakeUsageClient:
    pass


class _FakeMemoryProvisioner:
    pass


def _create_table(ddb: Any, table_name: str) -> None:
    ddb.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _failover_event(lock_id: str) -> dict[str, Any]:
    return {
        "httpMethod": "POST",
        "path": "/v1/platform/failover",
        "body": json.dumps({"targetRegion": "eu-central-1", "lockId": lock_id}),
        "requestContext": {
            "authorizer": {
                "tenantid": "t-admin",
                "appid": "app-admin",
                "tier": "premium",
                "sub": "user-123",
                "roles": ["Platform.Admin"],
            }
        },
    }


def test_failover_route_updates_ssm_and_bridge_reads_new_runtime_region(monkeypatch) -> None:
    with mock_aws():
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
        monkeypatch.setenv("AWS_REGION", "eu-west-2")
        monkeypatch.setenv("OPS_LOCKS_TABLE", "platform-ops-locks")
        monkeypatch.setenv("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")

        ddb_resource = boto3.resource("dynamodb", region_name="eu-west-2")
        ddb_client = boto3.client("dynamodb", region_name="eu-west-2")
        ssm = boto3.client("ssm", region_name="eu-west-2")
        _create_table(ddb_resource, "platform-ops-locks")
        ssm.put_parameter(Name="/platform/config/runtime-region", Value="eu-west-1", Type="String")
        ssm.put_parameter(
            Name="/platform/config/mock-runtime-url",
            Value="http://localhost:8765",
            Type="String",
        )

        lock_record = failover_lock.acquire_lock(
            ddb_client,
            table_name="platform-ops-locks",
            acquired_by="ops@example.com",
        )

        deps = tenant_api_handler.TenantApiDependencies(
            secretsmanager=_FakeSecretsManager(),
            events=_FakeEvents(),
            dynamodb=ddb_resource,
            ssm=ssm,
            usage_client=_FakeUsageClient(),
            memory_provisioner=_FakeMemoryProvisioner(),
        )
        monkeypatch.setattr(tenant_api_handler, "_dependencies", lambda: deps)

        response = tenant_api_handler.lambda_handler(
            _failover_event(lock_record.lock_id), FakeLambdaContext()
        )

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["status"] == "completed"

        bridge_handler._ssm_client = None
        bridge_handler._config_cache = {}
        bridge_handler._config_cache_expiry = 0
        config = bridge_handler.get_config(force_refresh=True)

        assert config["runtime_region"] == "eu-central-1"
