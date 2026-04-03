from __future__ import annotations

from datetime import timedelta
from typing import Any

from data_access.models import TenantStatus

try:
    from . import (
        auth,
        constants,
        db_factory,
        db_utils,
        events,
        http_utils,
        lifecycle_logic,
        models,
        secrets_manager,
        serialization,
        utils,
        validation,
    )
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import (
        auth,
        constants,
        db_factory,
        db_utils,
        events,
        http_utils,
        lifecycle_logic,
        models,
        secrets_manager,
        serialization,
        utils,
        validation,
    )


def handle_create(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    auth.require_admin(caller)
    body = http_utils.require_json_body(event)
    required = ["tenantId", "appId", "displayName", "tier", "ownerEmail", "ownerTeam", "accountId"]
    missing = [field for field in required if utils.str_or_none(body.get(field)) is None]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")

    tenant_id = validation.canonical_tenant_id(body["tenantId"])
    app_id = str(body["appId"]).strip()
    now = utils.now_utc()
    tier = lifecycle_logic.normalize_tier(body.get("tier"))

    if db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller, app_id=app_id) is not None:
        return http_utils.error(409, "CONFLICT", "Tenant already exists")

    memory_info = deps.memory_provisioner.provision(tenant_id=tenant_id, app_id=app_id) or {}
    api_key_secret_arn = secrets_manager.create_api_key_secret(
        deps, tenant_id=tenant_id, app_id=app_id
    )

    attributes: dict[str, Any] = {
        "tenantId": tenant_id,
        "appId": app_id,
        "displayName": str(body["displayName"]).strip(),
        "tier": tier,
        "status": TenantStatus.ACTIVE.value,
        "createdAt": utils.iso(now),
        "updatedAt": utils.iso(now),
        "provisioningStatus": "pending",
        "provisioningUpdatedAt": utils.iso(now),
        "ownerEmail": str(body["ownerEmail"]).strip(),
        "ownerTeam": str(body["ownerTeam"]).strip(),
        "accountId": str(body["accountId"]).strip(),
        "apiKeySecretArn": api_key_secret_arn,
    }
    if body.get("monthlyBudgetUsd") is not None:
        attributes["monthlyBudgetUsd"] = utils.as_float(
            body["monthlyBudgetUsd"], field="monthlyBudgetUsd"
        )

    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    db.put_item(db_factory.tenants_table_name(), attributes)
    events.put_event(
        deps,
        detail_type="platform.tenant.created",
        detail={
            "tenantId": tenant_id,
            "appId": app_id,
            "tier": tier,
            "accountId": attributes["accountId"],
            "memoryInfo": memory_info,
        },
    )
    return http_utils.response(201, serialization.serialize_tenant(attributes))


def handle_read(
    caller: models.CallerIdentity,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not auth.can_read_tenant(caller, tenant_id):
        raise PermissionError("Access denied")

    item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    return http_utils.response(200, serialization.serialize_tenant(item))


def handle_update(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    _ = deps
    auth.require_admin(caller)
    body = http_utils.require_json_body(event)

    item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    now = utils.now_utc()
    updates: dict[str, Any] = {"updatedAt": utils.iso(now)}
    if "displayName" in body:
        updates["displayName"] = str(body["displayName"]).strip()
    if "status" in body:
        updates["status"] = lifecycle_logic.normalize_status(body["status"])
    if "tier" in body:
        updates["tier"] = lifecycle_logic.normalize_tier(body["tier"])
    if "monthlyBudgetUsd" in body:
        updates["monthlyBudgetUsd"] = utils.as_float(
            body["monthlyBudgetUsd"], field="monthlyBudgetUsd"
        )

    expression, names, values = db_utils.build_update_expression(updates)
    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    db.update_item(
        db_factory.tenants_table_name(),
        db_utils.tenant_key(tenant_id),
        expression,
        values,
        expression_attribute_names=names,
    )
    updated_item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    return http_utils.response(200, serialization.serialize_tenant(updated_item or item))


def handle_delete(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    _ = deps
    auth.require_admin(caller)

    item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    now = utils.now_utc()
    updates = {
        "status": "deleted",
        "deletedAt": utils.iso(now),
        "purgeAtEpochSeconds": int(
            (now + timedelta(days=constants.DELETE_RETENTION_DAYS)).timestamp()
        ),
        "updatedAt": utils.iso(now),
    }
    expression, names, values = db_utils.build_update_expression(updates)
    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    db.update_item(
        db_factory.tenants_table_name(),
        db_utils.tenant_key(tenant_id),
        expression,
        values,
        expression_attribute_names=names,
    )
    return http_utils.response(204, {})
