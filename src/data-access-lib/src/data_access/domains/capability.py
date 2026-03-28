from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256

from data_access.domains.tenant import TenantTier


class ConfigurationStore(StrEnum):
    APPCONFIG = "appconfig"
    SSM = "ssm"
    DYNAMODB = "dynamodb"


APPCONFIG_DYNAMIC_CAPABILITY_AREAS = frozenset(
    {
        "tier_feature_enablement",
        "capability_flags",
        "kill_switches",
        "model_availability",
        "tool_availability",
        "rollout_controls",
    }
)
SSM_PLATFORM_PARAMETER_AREAS = frozenset(
    {
        "runtime_region_parameters",
        "operational_failover_parameters",
        "service_endpoints",
        "appconfig_bootstrap",
    }
)
DYNAMODB_TENANT_METADATA_AREAS = frozenset(
    {
        "tenant_state",
        "tenant_resource_inventory",
        "tenant_identity_metadata",
        "tenant_budget_contracts",
        "invocation_audit",
        "job_tracking",
        "session_tracking",
    }
)


def configuration_store_for(area: str) -> ConfigurationStore:
    normalized = area.strip().lower()
    if normalized in APPCONFIG_DYNAMIC_CAPABILITY_AREAS:
        return ConfigurationStore.APPCONFIG
    if normalized in SSM_PLATFORM_PARAMETER_AREAS:
        return ConfigurationStore.SSM
    if normalized in DYNAMODB_TENANT_METADATA_AREAS:
        return ConfigurationStore.DYNAMODB
    raise ValueError(f"Unknown configuration area: {area!r}")


@dataclass(frozen=True)
class CapabilityRollout:
    enabled: bool = False
    rollout_percentage: int = 100
    tier_allow_list: frozenset[TenantTier] = field(default_factory=frozenset)
    tenant_allow_list: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not 0 <= self.rollout_percentage <= 100:
            raise ValueError("rollout_percentage must be between 0 and 100")

    def is_enabled_for(self, *, tenant_id: str, tenant_tier: TenantTier) -> bool:
        if not self.enabled:
            return False

        normalized_tenant_id = tenant_id.strip().lower()
        if normalized_tenant_id in self.tenant_allow_list:
            return True
        if self.tier_allow_list and tenant_tier not in self.tier_allow_list:
            return False
        if self.rollout_percentage == 0:
            return False
        if self.rollout_percentage == 100:
            return True

        bucket = (
            int.from_bytes(
                sha256(normalized_tenant_id.encode("utf-8")).digest()[:4],
                byteorder="big",
            )
            % 100
        )
        return bucket < self.rollout_percentage


@dataclass(frozen=True)
class TenantCapabilityPolicy:
    schema_version: str = "2026-03-21"
    capabilities: dict[str, CapabilityRollout] = field(default_factory=dict)
    killed_capabilities: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def safe_fallback(cls) -> TenantCapabilityPolicy:
        return cls()

    def is_enabled(self, capability: str, *, tenant_id: str, tenant_tier: TenantTier) -> bool:
        normalized_capability = capability.strip().lower()
        if not normalized_capability:
            return False
        if normalized_capability in self.killed_capabilities:
            return False

        rollout = self.capabilities.get(normalized_capability)
        if rollout is None:
            return False
        return rollout.is_enabled_for(tenant_id=tenant_id, tenant_tier=tenant_tier)
