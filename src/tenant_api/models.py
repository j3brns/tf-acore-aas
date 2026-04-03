from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.tenant_api.constants import ADMIN_ROLES


@dataclass(frozen=True)
class CallerIdentity:
    tenant_id: str | None
    app_id: str | None
    tier: str | None
    sub: str | None
    roles: frozenset[str]
    usage_identifier_key: str | None

    @property
    def is_admin(self) -> bool:
        return bool(self.roles & ADMIN_ROLES)

    @property
    def is_platform_actor(self) -> bool:
        from src.tenant_api.constants import PLATFORM_TENANT_ID

        return self.tenant_id == PLATFORM_TENANT_ID


@dataclass(frozen=True)
class TenantApiDependencies:
    secretsmanager: Any
    events: Any
    ssm: Any
    awslambda: Any
    usage_client: Any
    memory_provisioner: Any
    platform_quota_client: Any
