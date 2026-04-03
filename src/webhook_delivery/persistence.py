from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from data_access import TenantContext, TenantScopedDynamoDB
from data_access.models import TenantTier

from . import events


def load_retry_job(
    *,
    jobs_table: str,
    tenant_id: str,
    app_id: str,
    job_id: str,
) -> dict[str, Any] | None:
    tenant_context = TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=TenantTier.BASIC,
        sub="webhook-delivery",
    )
    db = TenantScopedDynamoDB(tenant_context)
    return db.get_item(jobs_table, {"PK": f"TENANT#{tenant_id}", "SK": f"JOB#{job_id}"})


def tenant_context_from_job(job_item: dict[str, Any]) -> TenantContext:
    tenant_id = events.coerce_optional_string(job_item.get("tenant_id"))
    app_id = events.coerce_optional_string(job_item.get("app_id"))
    if tenant_id is None or app_id is None:
        raise ValueError("Job record is missing tenant_id or app_id")
    return TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=TenantTier.BASIC,
        sub="webhook-delivery",
    )


def get_webhook_registration(
    *,
    tenants_table: str,
    tenant_context: TenantContext,
    webhook_id: str,
) -> dict[str, Any] | None:
    db = TenantScopedDynamoDB(tenant_context)
    record = db.get_item(
        tenants_table,
        {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": f"WEBHOOK#{webhook_id}"},
    )
    if record is None:
        return None
    if str(record.get("tenant_id", "")) != tenant_context.tenant_id:
        return None
    return record


def mark_delivery_state(
    *,
    jobs_table: str,
    tenant_context: TenantContext,
    job_id: str,
    delivered: bool,
    status: str,
    attempts: int,
    error: str | None,
) -> None:
    db = TenantScopedDynamoDB(tenant_context)
    db.update_item(
        jobs_table,
        {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": f"JOB#{job_id}"},
        (
            "SET webhook_delivered = :delivered, "
            "webhook_delivery_status = :status, "
            "webhook_delivery_attempts = :attempts, "
            "webhook_delivery_error = :error, "
            "webhook_last_attempt_at = :last_attempt_at"
        ),
        {
            ":delivered": delivered,
            ":status": status,
            ":attempts": attempts,
            ":error": error,
            ":last_attempt_at": datetime.now(UTC).isoformat(),
        },
    )
