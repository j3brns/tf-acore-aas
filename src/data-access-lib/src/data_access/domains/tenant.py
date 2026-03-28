from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TenantTier(StrEnum):
    BASIC = "basic"
    STANDARD = "standard"
    PREMIUM = "premium"


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class WebhookStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class InviteStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True)
class TenantRecord:
    tenant_id: str
    app_id: str
    display_name: str
    tier: TenantTier
    status: TenantStatus
    created_at: str
    updated_at: str
    owner_email: str
    owner_team: str
    account_id: str
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


@dataclass(frozen=True)
class PaginatedItems:
    items: list[dict[str, Any]]
    last_evaluated_key: dict[str, Any] | None = None


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    app_id: str
    tier: TenantTier
    sub: str


@dataclass(frozen=True)
class WebhookRecord:
    webhook_id: str
    tenant_id: str
    callback_url: str
    events: list[str]
    status: WebhookStatus
    created_at: str
    updated_at: str
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
    invite_id: str
    tenant_id: str
    email: str
    role: str
    status: InviteStatus
    created_at: str
    expires_at: str
    display_name: str | None = None

    @property
    def pk(self) -> str:
        return f"TENANT#{self.tenant_id}"

    @property
    def sk(self) -> str:
        return f"INVITE#{self.invite_id}"
