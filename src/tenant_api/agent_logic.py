from __future__ import annotations

from typing import Any

from data_access.models import AGENT_STATUS_TRANSITIONS, AgentStatus, normalize_agent_status

from src.tenant_api.constants import PLATFORM_TENANT_ID
from src.tenant_api.models import CallerIdentity
from src.tenant_api.utils import iso, now_utc, str_or_none


def normalize_agent_status_val(value: Any) -> AgentStatus:
    try:
        return normalize_agent_status(str_or_none(value))
    except ValueError as exc:
        allowed = ", ".join(status.value for status in AgentStatus)
        raise ValueError(f"status must be one of: {allowed}") from exc


def validate_agent_status_transition(
    current_status: AgentStatus,
    new_status: AgentStatus,
) -> None:
    if new_status == current_status:
        return
    allowed = AGENT_STATUS_TRANSITIONS.get(current_status, frozenset())
    if new_status not in allowed:
        allowed_text = ", ".join(status.value for status in sorted(allowed, key=lambda s: s.value))
        raise ValueError(
            f"Invalid agent status transition: {current_status.value} -> {new_status.value}. "
            f"Allowed transitions: {allowed_text or 'none'}"
        )


def agent_event_detail_type(status: AgentStatus) -> str | None:
    if status is AgentStatus.PROMOTED:
        return "platform.agent_version.promoted"
    if status is AgentStatus.ROLLED_BACK:
        return "platform.agent_version.rolled_back"
    return None


def agent_release_operation(status: AgentStatus) -> str | None:
    if status is AgentStatus.PROMOTED:
        return "promotion"
    if status is AgentStatus.ROLLED_BACK:
        return "rollback"
    return None


def _platform_audit_envelope(
    *,
    caller: CallerIdentity,
    operation_type: str,
    outcome: str,
    target_tenant_id: str | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    detail = {
        "schemaVersion": 1,
        "occurredAt": occurred_at or iso(now_utc()),
        "actorTenantId": PLATFORM_TENANT_ID,
        "actorAppId": caller.app_id,
        "actorSub": caller.sub,
        "operationType": operation_type,
        "outcome": outcome,
    }
    if target_tenant_id is not None:
        detail["targetTenantId"] = target_tenant_id
    return detail


def build_agent_release_lifecycle_event_detail(
    *,
    caller: CallerIdentity,
    agent_name: str,
    version: str,
    previous_status: AgentStatus,
    new_status: AgentStatus,
    occurred_at: str,
    approved_by: str | None,
    approved_at: str | None,
    release_notes: str | None,
    evaluation_score: float | None,
    evaluation_report_url: str | None,
    rolled_back_by: str | None,
    rolled_back_at: str | None,
) -> dict[str, Any]:
    detail = _platform_audit_envelope(
        caller=caller,
        operation_type=agent_release_operation(new_status) or "agent_release",
        outcome="succeeded",
        target_tenant_id=PLATFORM_TENANT_ID,
        occurred_at=occurred_at,
    )
    detail.update(
        {
            "operation": agent_release_operation(new_status),
            "releaseId": f"{agent_name}:{version}",
            "agentRecordPk": f"AGENT#{agent_name}",
            "agentRecordSk": f"VERSION#{version}",
            "agentName": agent_name,
            "version": version,
            "previousStatus": previous_status.value,
            "status": new_status.value,
            "approvedBy": approved_by,
            "approvedAt": approved_at,
            "releaseNotes": release_notes,
            "evaluationScore": evaluation_score,
            "evaluationReportUrl": evaluation_report_url,
            "rolledBackBy": rolled_back_by,
            "rolledBackAt": rolled_back_at,
            "targetResourceType": "agentVersion",
            "targetResourceId": f"{agent_name}:{version}",
        }
    )
    return detail
