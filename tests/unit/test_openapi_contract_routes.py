from __future__ import annotations

from pathlib import Path

import yaml


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
