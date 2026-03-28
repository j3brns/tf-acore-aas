from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from data_access.domains.tenant import TenantTier


class InvocationMode(StrEnum):
    SYNC = "sync"
    STREAMING = "streaming"
    ASYNC = "async"


class AgentStatus(StrEnum):
    BUILT = "built"
    DEPLOYED_STAGING = "deployed_staging"
    INTEGRATION_VERIFIED = "integration_verified"
    EVALUATION_PASSED = "evaluation_passed"
    APPROVED = "approved"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


LEGACY_AGENT_STATUS_ALIASES: dict[str, AgentStatus] = {
    "pending": AgentStatus.BUILT,
    "released": AgentStatus.PROMOTED,
    "rollback": AgentStatus.ROLLED_BACK,
}
REGISTERABLE_AGENT_STATUSES = frozenset({AgentStatus.BUILT})
INVOKABLE_AGENT_STATUSES = frozenset({AgentStatus.PROMOTED})
AGENT_STATUS_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.BUILT: frozenset({AgentStatus.DEPLOYED_STAGING, AgentStatus.FAILED}),
    AgentStatus.DEPLOYED_STAGING: frozenset({AgentStatus.INTEGRATION_VERIFIED, AgentStatus.FAILED}),
    AgentStatus.INTEGRATION_VERIFIED: frozenset(
        {AgentStatus.EVALUATION_PASSED, AgentStatus.FAILED}
    ),
    AgentStatus.EVALUATION_PASSED: frozenset({AgentStatus.APPROVED, AgentStatus.FAILED}),
    AgentStatus.APPROVED: frozenset({AgentStatus.PROMOTED, AgentStatus.FAILED}),
    AgentStatus.PROMOTED: frozenset({AgentStatus.ROLLED_BACK}),
    AgentStatus.ROLLED_BACK: frozenset(),
    AgentStatus.FAILED: frozenset(),
}


def normalize_agent_status(
    value: AgentStatus | str | None,
    *,
    default: AgentStatus | None = None,
) -> AgentStatus:
    if isinstance(value, AgentStatus):
        return value
    if value is None:
        if default is not None:
            return default
        raise ValueError("status is required")

    normalized = str(value).strip().lower()
    if not normalized:
        if default is not None:
            return default
        raise ValueError("status is required")
    if normalized in LEGACY_AGENT_STATUS_ALIASES:
        return LEGACY_AGENT_STATUS_ALIASES[normalized]
    return AgentStatus(normalized)


def is_invokable_agent_status(value: AgentStatus | str | None) -> bool:
    return normalize_agent_status(value, default=AgentStatus.PROMOTED) in INVOKABLE_AGENT_STATUSES


@dataclass(frozen=True)
class AgentRecord:
    agent_name: str
    version: str
    owner_team: str
    tier_minimum: TenantTier
    layer_hash: str
    layer_s3_key: str
    script_s3_key: str
    deployed_at: str
    invocation_mode: InvocationMode
    streaming_enabled: bool
    status: AgentStatus = AgentStatus.BUILT
    approved_by: str | None = None
    approved_at: str | None = None
    release_notes: str | None = None
    runtime_arn: str | None = None
    estimated_duration_seconds: int | None = None
    commit_sha: str | None = None
    pipeline_url: str | None = None
    job_id: str | None = None
    evaluation_score: float | None = None
    evaluation_report_url: str | None = None
    rolled_back_by: str | None = None
    rolled_back_at: str | None = None

    @property
    def pk(self) -> str:
        return f"AGENT#{self.agent_name}"

    @property
    def sk(self) -> str:
        return f"VERSION#{self.version}"
