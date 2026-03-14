from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import handler as tenant_api_handler


def _load_openapi() -> dict:
    spec_path = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"
    with spec_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_openapi_declares_canonical_invoke_and_jobs_routes() -> None:
    spec = _load_openapi()
    paths = spec.get("paths", {})

    assert "/v1/agents/{agentName}/invoke" in paths
    assert "/v1/jobs/{jobId}" in paths
    assert "/v1/invoke" not in paths
    assert "/v1/sessions" not in paths

    invoke_operation = paths["/v1/agents/{agentName}/invoke"].get("post", {})
    assert invoke_operation.get("operationId") == "invokeAgent"

    jobs_operation = paths["/v1/jobs/{jobId}"].get("get", {})
    assert jobs_operation.get("operationId") == "getJob"


def test_openapi_async_invoke_response_contract_points_to_jobs_polling() -> None:
    spec = _load_openapi()
    components = spec.get("components", {})
    schemas = components.get("schemas", {})
    async_schema = schemas.get("AgentInvokeAsyncAccepted", {})
    required = async_schema.get("required", [])
    properties = async_schema.get("properties", {})

    assert "jobId" in required
    assert "pollUrl" in required
    assert "mode" in required
    assert properties.get("mode", {}).get("enum") == ["async"]


def test_openapi_tenant_id_contract_is_deterministic_and_env_safe() -> None:
    spec = _load_openapi()
    components = spec.get("components", {})

    tenant_param = components.get("parameters", {}).get("TenantId", {})
    tenant_param_schema = tenant_param.get("schema", {})
    assert tenant_param_schema.get("minLength") == 3
    assert tenant_param_schema.get("maxLength") == 32
    assert tenant_param_schema.get("pattern") == "^[a-z](?:[a-z0-9-]{1,30}[a-z0-9])$"

    tenant_create_schema = (
        components.get("schemas", {}).get("TenantCreateRequest", {}).get("properties", {})
    )
    tenant_id_schema = tenant_create_schema.get("tenantId", {})
    assert tenant_id_schema.get("pattern") == "^[a-z](?:[a-z0-9-]{1,30}[a-z0-9])$"


def test_openapi_bff_token_refresh_contract_restricts_to_platform_scopes() -> None:
    spec = _load_openapi()
    schema = spec.get("components", {}).get("schemas", {}).get("BffTokenRefreshRequest", {})
    properties = schema.get("properties", {})

    assert schema.get("required") == ["scopes"]
    assert "audience" not in properties
    assert "sessionId" not in properties
    scope_items = properties.get("scopes", {}).get("items", {})
    assert scope_items.get("pattern") == "^api://[A-Za-z0-9-]+/[A-Za-z][A-Za-z0-9._-]{0,127}$"


def test_openapi_split_accounts_target_account_id_pattern_matches_runtime_validator() -> None:
    spec = _load_openapi()
    schema = (
        spec.get("paths", {})
        .get("/v1/platform/quota/split-accounts", {})
        .get("post", {})
        .get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    target_account_schema = schema.get("properties", {}).get("targetAccountId", {})

    assert target_account_schema.get("pattern") == "^[0-9]{12}$"
    assert tenant_api_handler._AWS_ACCOUNT_ID_PATTERN.pattern == "^[0-9]{12}$"
