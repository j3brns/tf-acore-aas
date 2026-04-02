from __future__ import annotations

from typing import Any

from data_access.models import TenantStatus, TenantTier

from src.tenant_api.agent_logic import _platform_audit_envelope
from src.tenant_api.constants import ALLOWED_TENANT_INVITE_ROLES
from src.tenant_api.http_utils import response, str_or_none
from src.tenant_api.models import CallerIdentity


def normalize_tier(value: Any) -> str:
    tier_text = str_or_none(value)
    if tier_text is None:
        raise ValueError("tier is required")
    try:
        return TenantTier(tier_text.lower()).value
    except ValueError as exc:
        raise ValueError("tier must be one of: basic, standard, premium") from exc


def normalize_status(value: Any) -> str:
    status_text = str_or_none(value)
    if status_text is None:
        raise ValueError("status is required")
    try:
        return TenantStatus(status_text.lower()).value
    except ValueError as exc:
        allowed = ", ".join(status.value for status in TenantStatus)
        raise ValueError(f"status must be one of: {allowed}") from exc


def normalize_tenant_invite_role(value: Any) -> str:
    role = str_or_none(value) or "Agent.Invoke"
    if role not in ALLOWED_TENANT_INVITE_ROLES:
        allowed = ", ".join(sorted(ALLOWED_TENANT_INVITE_ROLES))
        raise ValueError(f"role must be one of: {allowed}")
    return role


def platform_control_response(
    status_code: int,
    body: dict[str, Any],
    *,
    caller: CallerIdentity,
    operation_type: str,
    target_tenant_id: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    payload = dict(body)
    payload["audit"] = _platform_audit_envelope(
        caller=caller,
        operation_type=operation_type,
        outcome=outcome or ("succeeded" if status_code < 400 else "failed"),
        target_tenant_id=target_tenant_id,
    )
    return response(status_code, payload)
