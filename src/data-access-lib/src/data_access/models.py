"""
data_access.models — DynamoDB table schemas as Python dataclasses.

Defines the canonical data model for all platform DynamoDB tables.
PRESENT FOR REVIEW before writing any Lambda code (Phase 1 gate).

Tables defined here:
    platform-tenants       — tenant registry (provisioned, auto-scaling, 5 RCU/WCU)
    platform-agents        — agent registry  (provisioned, auto-scaling)
    platform-invocations   — invocation audit log (on-demand, TTL 90 days)
    platform-jobs          — async job tracking   (on-demand, TTL 7 days)
    platform-sessions      — active session tracking (TTL 24h after last activity)
    platform-tools         — Gateway tool registry
    platform-ops-locks     — distributed operation locks (TTL 5 min)

ADR: ADR-012 — On-Demand for Invocations, Provisioned for Config Tables
Implemented in TASK-011.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# ---------------------------------------------------------------------------
# TTL constants (seconds)
# ---------------------------------------------------------------------------
INVOCATION_TTL_SECONDS: int = 90 * 24 * 60 * 60  # 90 days
JOB_TTL_SECONDS: int = 7 * 24 * 60 * 60  # 7 days
SESSION_TTL_SECONDS: int = 24 * 60 * 60  # 24 hours
OPS_LOCK_TTL_SECONDS: int = 5 * 60  # 5 minutes

# Jitter suffix length for high-volume tenant hot-partition mitigation (ADR-012)
JITTER_LENGTH: int = 2


# ---------------------------------------------------------------------------
# Enums — constrained vocabulary for status/type fields
# ---------------------------------------------------------------------------


class TenantTier(StrEnum):
    BASIC = "basic"
    STANDARD = "standard"
    PREMIUM = "premium"


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class InvocationMode(StrEnum):
    SYNC = "sync"
    STREAMING = "streaming"
    ASYNC = "async"


class InvocationStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    THROTTLED = "throttled"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Table: platform-tenants
# PK: TENANT#{tenantId}  SK: METADATA
# Capacity: provisioned, auto-scaling, 5 RCU/WCU minimum
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantRecord:
    """Tenant registry record.

    Required at creation: tenant_id, app_id, display_name, tier, status,
    created_at, updated_at, owner_email, owner_team, account_id.
    Infrastructure ARNs are optional until TenantStack is deployed.
    """

    tenant_id: str
    app_id: str
    display_name: str
    tier: TenantTier
    status: TenantStatus
    created_at: str  # ISO 8601 UTC
    updated_at: str  # ISO 8601 UTC
    owner_email: str
    owner_team: str
    account_id: str  # AWS account ID for tenant Runtime
    memory_store_arn: str | None = None
    runtime_region: str | None = None
    fallback_region: str | None = None
    api_key_secret_arn: str | None = None
    monthly_budget_usd: float | None = None

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenant_id}"

    @property
    def sk(self) -> str:
        return "METADATA"


# ---------------------------------------------------------------------------
# Table: platform-agents
# PK: AGENT#{agentName}  SK: VERSION#{semver}
# Capacity: provisioned, auto-scaling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentRecord:
    """Agent registry record — one record per deployed version.

    tier_minimum: the lowest tenant tier that may invoke this agent.
    invocation_mode: declared in pyproject.toml, never inferred at runtime.
    """

    agent_name: str
    version: str  # semver e.g. "1.2.3"
    owner_team: str
    tier_minimum: TenantTier
    layer_hash: str  # first 16 hex chars of SHA256 over sorted deps
    layer_s3_key: str
    script_s3_key: str
    deployed_at: str  # ISO 8601 UTC
    invocation_mode: InvocationMode
    streaming_enabled: bool
    runtime_arn: str | None = None
    estimated_duration_seconds: int | None = None

    @property
    def pk(self) -> str:
        return f"AGENT#{self.agent_name}"

    @property
    def sk(self) -> str:
        return f"VERSION#{self.version}"


# ---------------------------------------------------------------------------
# Table: platform-invocations
# PK: TENANT#{tenantId}  SK: INV#{timestamp}#{invocationId}[#{jitter}]
# Capacity: on-demand. TTL: 90 days.
# Hot-partition mitigation: jitter suffix added for tenants >1000 req/min.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvocationRecord:
    """Invocation audit log record.

    jitter: 2-character random hex suffix appended to SK for high-volume
    tenants to prevent DynamoDB hot-partition on the TENANT# key.
    Must be exactly JITTER_LENGTH chars when set (see ADR-012).
    """

    invocation_id: str
    tenant_id: str
    app_id: str
    agent_name: str
    agent_version: str
    session_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    status: InvocationStatus
    runtime_region: str
    invocation_mode: InvocationMode
    timestamp: str  # ISO 8601 UTC — embedded in SK for range queries
    ttl: int  # Unix epoch seconds (created_at + INVOCATION_TTL_SECONDS)
    jitter: str | None = None
    error_code: str | None = None
    job_id: str | None = None

    def __post_init__(self) -> None:
        if self.jitter is not None and len(self.jitter) != JITTER_LENGTH:
            raise ValueError(
                f"jitter must be exactly {JITTER_LENGTH} characters, got {len(self.jitter)!r}"
            )

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenant_id}"

    @property
    def sk(self) -> str:
        base = f"INV#{self.timestamp}#{self.invocation_id}"
        if self.jitter:
            return f"{base}#{self.jitter}"
        return base


# ---------------------------------------------------------------------------
# Table: platform-jobs
# PK: JOB#{jobId}  SK: METADATA
# Capacity: on-demand. TTL: 7 days.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobRecord:
    """Async job tracking record.

    Lifecycle: PENDING → RUNNING → COMPLETED | FAILED
    result_s3_key: set on COMPLETED, presigned URL served via GET /v1/jobs/{id}.
    webhook_delivered: flipped to True by webhook-delivery Lambda on success.
    """

    job_id: str
    tenant_id: str
    agent_name: str
    status: JobStatus
    created_at: str  # ISO 8601 UTC
    ttl: int  # Unix epoch seconds (created_at + JOB_TTL_SECONDS)
    started_at: str | None = None
    completed_at: str | None = None
    result_s3_key: str | None = None
    error_message: str | None = None
    webhook_url: str | None = None
    webhook_delivered: bool = False

    @property
    def pk(self) -> str:
        return f"JOB#{self.job_id}"

    @property
    def sk(self) -> str:
        return "METADATA"


# ---------------------------------------------------------------------------
# Table: platform-sessions
# PK: TENANT#{tenantId}  SK: SESSION#{sessionId}
# TTL: 24 hours after last_activity_at.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRecord:
    """Active session tracking record.

    ttl is recomputed on each keepalive write:
    ttl = unix_epoch(last_activity_at) + SESSION_TTL_SECONDS
    """

    session_id: str
    tenant_id: str
    runtime_session_id: str  # AgentCore Runtime session identifier
    agent_name: str
    started_at: str  # ISO 8601 UTC
    last_activity_at: str  # ISO 8601 UTC — updated on keepalive
    status: SessionStatus
    ttl: int  # Unix epoch seconds

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenant_id}"

    @property
    def sk(self) -> str:
        return f"SESSION#{self.session_id}"


# ---------------------------------------------------------------------------
# Table: platform-tools
# PK: TOOL#{toolName}  SK: TENANT#{tenantId}  or  SK: GLOBAL
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolRecord:
    """Gateway tool registry record.

    tenant_id=None means the tool is available globally to all tenants
    at or above tier_minimum.  When set, the tool is restricted to that
    tenant only (custom/private tool deployment).
    """

    tool_name: str
    tier_minimum: TenantTier
    lambda_arn: str
    gateway_target_id: str
    enabled: bool
    tenant_id: str | None = None

    @property
    def pk(self) -> str:
        return f"TOOL#{self.tool_name}"

    @property
    def sk(self) -> str:
        if self.tenant_id:
            return f"TENANT#{self.tenant_id}"
        return "GLOBAL"


# ---------------------------------------------------------------------------
# Table: platform-ops-locks
# PK: LOCK#{lockName}  SK: METADATA
# Capacity: provisioned, 1 RCU/WCU. TTL: 5 minutes (auto-expire).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpsLockRecord:
    """Distributed operation lock record.

    Used for: region failover, account scaling transitions.
    TTL ensures stale locks self-expire within 5 minutes even if
    the lock-holder crashes before releasing.
    acquired_by: identity string (e.g. "ops/failover-lock.py@hostname")
    """

    lock_name: str
    lock_id: str  # UUID — used to prevent stale-lock release
    acquired_by: str
    acquired_at: str  # ISO 8601 UTC
    ttl: int  # Unix epoch seconds (acquired_at + OPS_LOCK_TTL_SECONDS)

    @property
    def pk(self) -> str:
        return f"LOCK#{self.lock_name}"

    @property
    def sk(self) -> str:
        return "METADATA"


# ---------------------------------------------------------------------------
# TenantContext — runtime identity injected by the Authoriser Lambda
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantContext:
    """
    Tenant identity context returned by the Authoriser Lambda.

    Passed to TenantScopedDynamoDB and TenantScopedS3 to scope all
    data access to the authenticated tenant's partition.

    Fields align with the authoriser response context:
        tenantid, appid, tier, sub.
    """

    tenant_id: str
    app_id: str
    tier: TenantTier
    sub: str  # JWT subject (user ID or machine identity)
