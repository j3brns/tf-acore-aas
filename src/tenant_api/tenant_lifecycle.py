from __future__ import annotations

import secrets
from typing import Any

from data_access.models import TenantStatus

try:
    from . import (
        constants,
        db_factory,
        db_utils,
        http_utils,
        models,
        tenant_audit_exports,
        tenant_invites,
        tenant_records,
        tenant_sessions,
        utils,
    )
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import (
        constants,
        db_factory,
        db_utils,
        http_utils,
        models,
        tenant_audit_exports,
        tenant_invites,
        tenant_records,
        tenant_sessions,
        utils,
    )

tenant_audit_exports.secrets = secrets


def handle_create(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    return tenant_records.handle_create(event, caller, deps)


def handle_read(
    caller: models.CallerIdentity,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    return tenant_records.handle_read(caller, tenant_id=tenant_id)


def handle_update(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    return tenant_records.handle_update(event, caller, deps, tenant_id=tenant_id)


def handle_delete(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    return tenant_records.handle_delete(caller, deps, tenant_id=tenant_id)


def handle_tenant_provisioning_event(
    event: dict[str, Any],
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    detail = event.get("detail", {})
    tenant_id = utils.str_or_none(detail.get("tenantId"))
    app_id = utils.str_or_none(detail.get("appId"))
    if not tenant_id:
        raise ValueError("tenantId missing in provisioning event")

    status = utils.str_or_none(detail.get("status"))
    if status not in constants.TENANT_PROVISIONING_STATUSES:
        raise ValueError(f"Invalid provisioning status: {status}")

    now = utils.now_utc()
    updates: dict[str, Any] = {
        "provisioningStatus": status,
        "provisioningUpdatedAt": utils.iso(now),
        "updatedAt": utils.iso(now),
    }
    if status == "ready":
        updates["status"] = TenantStatus.ACTIVE.value
        if "executionRoleArn" in detail:
            updates["executionRoleArn"] = str(detail["executionRoleArn"])
        if "memoryStoreArn" in detail:
            updates["memoryStoreArn"] = str(detail["memoryStoreArn"])
    elif status == "failed":
        updates["provisioningError"] = str(detail.get("error", "Unknown error"))

    # System update (no caller identity required for EventBridge trigger)
    # But db_for_tenant expects a caller for context derivation.
    # Using a dummy system caller.
    system_caller = models.CallerIdentity(
        tenant_id=None,
        app_id=None,
        tier=None,
        sub="system",
        roles=frozenset(),
        usage_identifier_key=None,
    )

    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=system_caller, app_id=app_id)
    expression, names, values = db_utils.build_update_expression(updates)
    db.update_item(
        db_factory.tenants_table_name(),
        db_utils.tenant_key(tenant_id),
        expression,
        values,
        expression_attribute_names=names,
    )

    return {"status": "updated", "tenantId": tenant_id, "provisioningStatus": status}


def handle_health(deps: models.TenantApiDependencies) -> dict[str, Any]:
    # Basic service health
    _ = deps
    return http_utils.response(
        200,
        {
            "status": "operational",
            "timestamp": utils.iso(utils.now_utc()),
        },
    )


def handle_sessions(
    event: dict[str, Any],
    caller: models.CallerIdentity,
) -> dict[str, Any]:
    return tenant_sessions.handle_sessions(event, caller)


def handle_audit_export(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    return tenant_audit_exports.handle_audit_export(event, caller, tenant_id=tenant_id)


def handle_list_invites(
    caller: models.CallerIdentity,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    return tenant_invites.handle_list_invites(caller, tenant_id=tenant_id)


def dispatch_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    tenant_id: str | None,
) -> dict[str, Any] | None:
    if path == "/v1/tenants" and method == "POST":
        return handle_create(event, caller, deps)
    if tenant_id:
        if path == f"/v1/tenants/{tenant_id}" and method == "GET":
            return handle_read(caller, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}" and method == "PATCH":
            return handle_update(event, caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}" and method == "DELETE":
            return handle_delete(caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}/audit-export" and method == "GET":
            return handle_audit_export(event, caller, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}/users/invites" and method == "GET":
            return handle_list_invites(caller, tenant_id=tenant_id)

        # Dispatch sub-resources (webhooks, etc.)
        if path.startswith(f"/v1/tenants/{tenant_id}/webhooks"):
            try:
                from src.tenant_api import webhook_registry
            except (ImportError, ValueError):
                from . import webhook_registry
            return webhook_registry.dispatch_routes(path, method, event, caller, deps, tenant_id)

    return None
