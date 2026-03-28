from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from data_access.domains.agent import InvocationMode
from data_access.domains.tenant import TenantTier

INVOCATION_TTL_SECONDS: int = 90 * 24 * 60 * 60
JOB_TTL_SECONDS: int = 7 * 24 * 60 * 60
SESSION_TTL_SECONDS: int = 24 * 60 * 60
OPS_LOCK_TTL_SECONDS: int = 5 * 60
JITTER_LENGTH: int = 2


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


@dataclass(frozen=True)
class InvocationRecord:
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
    timestamp: str
    ttl: int
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


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    tenant_id: str
    app_id: str
    agent_name: str
    status: JobStatus
    created_at: str
    ttl: int
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


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    tenant_id: str
    runtime_session_id: str
    agent_name: str
    started_at: str
    last_activity_at: str
    status: SessionStatus
    ttl: int

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenant_id}"

    @property
    def sk(self) -> str:
        return f"SESSION#{self.session_id}"


@dataclass(frozen=True)
class ToolRecord:
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


@dataclass(frozen=True)
class OpsLockRecord:
    lock_name: str
    lock_id: str
    acquired_by: str
    acquired_at: str
    ttl: int

    @property
    def pk(self) -> str:
        return f"LOCK#{self.lock_name}"

    @property
    def sk(self) -> str:
        return "METADATA"


@dataclass(frozen=True)
class BillingSummaryRecord:
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
