from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.tenant_api import dependency_factories


class FakeSession:
    def __init__(self) -> None:
        self.clients: dict[str, Any] = {}

    def client(self, service_name: str, *, region_name: str | None = None) -> Any:
        key = f"{service_name}:{region_name or 'default'}"
        return self.clients.setdefault(key, object())


def test_build_tenant_api_dependencies_uses_session_factories(monkeypatch) -> None:
    session = FakeSession()

    monkeypatch.setattr(
        dependency_factories.boto3.session,
        "Session",
        lambda *, region_name: session if region_name == "eu-west-2" else None,
    )

    deps = dependency_factories.build_tenant_api_dependencies(region="eu-west-2")

    assert deps.secretsmanager is session.client("secretsmanager")
    assert deps.events is session.client("events")
    assert deps.ssm is session.client("ssm")
    assert deps.awslambda is session.client("lambda")
    assert isinstance(deps.usage_client, dependency_factories._NoopUsageClient)
    assert isinstance(deps.memory_provisioner, dependency_factories._NoopMemoryProvisioner)
    assert isinstance(deps.platform_quota_client, dependency_factories._AwsPlatformQuotaClient)
