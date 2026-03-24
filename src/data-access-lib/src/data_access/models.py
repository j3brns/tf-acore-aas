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

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any

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


class WebhookStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class AgentStatus(StrEnum):
    BUILT = "built"
    DEPLOYED_STAGING = "deployed_staging"
    INTEGRATION_VERIFIED = "integration_verified"
    EVALUATION_PASSED = "evaluation_passed"
    APPROVED = "approved"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class InviteStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


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


def configuration_store_for(area: str) -> ConfigurationStore:
    """Return the owning config store for a platform configuration concern.

    The mapping is intentionally coarse-grained. It documents the architectural
    ownership split for issue #303 instead of binding callers to individual
    attribute names, which may evolve independently of the store boundary.
    """

    normalized = area.strip().lower()
    if normalized in APPCONFIG_DYNAMIC_CAPABILITY_AREAS:
        return ConfigurationStore.APPCONFIG
    if normalized in SSM_PLATFORM_PARAMETER_AREAS:
        return ConfigurationStore.SSM
    if normalized in DYNAMODB_TENANT_METADATA_AREAS:
        return ConfigurationStore.DYNAMODB
    raise ValueError(f"Unknown configuration area: {area!r}")


def normalize_agent_status(
    value: AgentStatus | str | None,
    *,
    default: AgentStatus | None = None,
) -> AgentStatus:
    """Normalize canonical and legacy agent release states.

    Legacy aliases preserve compatibility with older registry records while the
    canonical lifecycle now models release governance explicitly.
    """

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
    """Return True when the release state is tenant-invokable."""

    return normalize_agent_status(value, default=AgentStatus.PROMOTED) in INVOKABLE_AGENT_STATUSES


@dataclass(frozen=True)
class CapabilityRollout:
    """Dynamic capability policy with deterministic tenant targeting.

    The control plane loads these rules from AppConfig. Missing or malformed
    policy must degrade safely to disabled capability state.
    """

    enabled: bool = False
    rollout_percentage: int = 100
    tier_allow_list: frozenset[TenantTier] = field(default_factory=frozenset)
    tenant_allow_list: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not 0 <= self.rollout_percentage <= 100:
            raise ValueError("rollout_percentage must be between 0 and 100")

    def is_enabled_for(self, *, tenant_id: str, tenant_tier: TenantTier) -> bool:
        """Evaluate the rollout using tenant-safe defaults.

        Rules:
        - disabled rollout returns False immediately
        - explicit tenant allow-list overrides all other targeting
        - tier allow-list gates eligibility before percentage rollout
        - percentage targeting is deterministic per tenant ID for stable rollout
        """

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
    """AppConfig-backed dynamic capability policy.

    This model is intentionally separate from TenantRecord. TenantRecord remains
    the source of truth for resource inventory, contractual tenant metadata, and
    transactional state. Dynamic capability policy uses deny-by-default fallback
    semantics so a failed AppConfig read does not accidentally enable access.
    """

    schema_version: str = "2026-03-21"
    capabilities: dict[str, CapabilityRollout] = field(default_factory=dict)
    killed_capabilities: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def safe_fallback(cls) -> TenantCapabilityPolicy:
        """Return the deny-by-default fallback policy used on read failure."""

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
    execution_role_arn: str | None = None
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
    status: canonical release state for an immutable built version (ADR-015).
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
# PK: TENANT#{tenantId}  SK: JOB#{jobId}
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
    app_id: str
    agent_name: str
    status: JobStatus
    created_at: str  # ISO 8601 UTC
    ttl: int  # Unix epoch seconds (created_at + JOB_TTL_SECONDS)
    started_at: str | None = None
    completed_at: str | None = None
    result_s3_key: str | None = None
    error_message: str | None = None
    webhook_id: str | None = None
    webhook_url: str | None = None
    webhook_delivered: bool = False
    webhook_delivery_status: str | None = None
    webhook_delivery_attempts: int = 0
    webhook_delivery_error: str | None = None
    webhook_last_attempt_at: str | None = None

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenant_id}"

    @property
    def sk(self) -> str:
        return f"JOB#{self.job_id}"


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
# Table: platform-billing (or BILLING# items in platform-tenants)
# PK: TENANT#{tenantId}  SK: BILLING#{yearMonth}
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BillingSummaryRecord:
    """Monthly billing summary for a tenant.

    year_month: ISO format e.g. "2026-03"
    total_input_tokens: cumulative for the month
    total_output_tokens: cumulative for the month
    total_cost_usd: cumulative for the month
    last_updated: ISO 8601 UTC
    """

    tenant_id: str
    year_month: str
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    last_updated: str

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenant_id}"

    @property
    def sk(self) -> str:
        return f"BILLING#{self.year_month}"


# ---------------------------------------------------------------------------
# Pagination — shared response structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaginatedItems:
    """A page of DynamoDB items with an optional resumption key."""

    items: list[dict[str, Any]]
    last_evaluated_key: dict[str, Any] | None = None


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


@dataclass(frozen=True)
class WebhookRecord:
    """Webhook registration record.

    Stored under PK: TENANT#{tenantId} SK: WEBHOOK#{webhookId}
    """

    webhook_id: str
    tenant_id: str
    callback_url: str
    events: list[str]
    status: WebhookStatus
    created_at: str  # ISO 8601 UTC
    updated_at: str  # ISO 8601 UTC
    description: str | None = None
    secret_arn: str | None = None

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenant_id}"

    @property
    def sk(self) -> str:
        return f"WEBHOOK#{self.webhook_id}"


@dataclass(frozen=True)
class InviteRecord:
    """Tenant user invitation record.

    Stored under PK: TENANT#{tenantId} SK: INVITE#{inviteId}
    """

    invite_id: str
    tenant_id: str
    email: str
    role: str
    status: InviteStatus
    created_at: str  # ISO 8601 UTC
    expires_at: str  # ISO 8601 UTC
    display_name: str | None = None

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenant_id}"

    @property
    def sk(self) -> str:
        return f"INVITE#{self.invite_id}"
