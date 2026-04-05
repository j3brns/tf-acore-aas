from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import timedelta
from typing import Any

from boto3.dynamodb.conditions import Key
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


def _shared_handler() -> Any | None:
    return sys.modules.get("src.tenant_api.handler") or sys.modules.get("handler")


def _db_for_tenant(*, tenant_id: str, caller: models.CallerIdentity, app_id: str | None):
    shared = _shared_handler()
    if shared is not None and hasattr(shared, "_db_for_tenant"):
        return shared._db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    return db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)


def _control_plane_db(caller: models.CallerIdentity):
    shared = _shared_handler()
    if shared is not None and hasattr(shared, "_control_plane_db"):
        return shared._control_plane_db(caller)
    return db_factory.control_plane_db(caller)


def _now_utc():
    shared = _shared_handler()
    if shared is not None and hasattr(shared, "_now_utc"):
        return shared._now_utc()
    return utils.now_utc()


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
    now = _now_utc()
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
    memory_store_arn = utils.str_or_none(memory_info.get("memoryStoreArn"))
    if memory_store_arn is not None:
        attributes["memoryStoreArn"] = memory_store_arn

    item = {
        **db_utils.tenant_key(tenant_id),
        **attributes,
    }

    # Save to DynamoDB
    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    db.put_item(db_factory.tenants_table_name(), item)

    # Emit event
    events.put_event(
        deps,
        detail_type="tenant.created",
        detail={
            "tenantId": tenant_id,
            "appId": app_id,
            "tier": tier,
            "accountId": attributes["accountId"],
            "memoryInfo": memory_info,
        },
    )

    return http_utils.response(201, {"tenant": serialization.serialize_tenant(item)})


def handle_read(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not auth.can_read_tenant(caller, tenant_id):
        raise PermissionError("Access denied")

    item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    tenant = serialization.serialize_tenant(item)
    usage = deps.usage_client.get_tenant_usage(
        tenant_id=tenant_id,
        app_id=utils.str_or_none(item.get("appId")),
    )
    tenant["usage"] = usage if isinstance(usage, dict) else {}
    if caller.usage_identifier_key:
        tenant["usage"]["usageIdentifierKey"] = caller.usage_identifier_key
    return http_utils.response(200, {"tenant": tenant})


def handle_list_tenants(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    if not caller.is_admin:
        if caller.tenant_id:
            response = handle_read(caller, deps, tenant_id=caller.tenant_id)
            if response["statusCode"] == 200:
                body = json.loads(response["body"])
                return http_utils.response(200, {"items": [body["tenant"]], "nextToken": None})
        return http_utils.response(200, {"items": [], "nextToken": None})

    query = event.get("queryStringParameters") or {}
    status_filter = utils.str_or_none(query.get("status")) if isinstance(query, dict) else None
    tier_filter = utils.str_or_none(query.get("tier")) if isinstance(query, dict) else None
    db = _control_plane_db(caller)
    items = db.scan_all(db_factory.tenants_table_name())
    records = [
        serialization.serialize_tenant(item)
        for item in items
        if item.get("SK") == "METADATA"
        and (status_filter is None or utils.str_or_none(item.get("status")) == status_filter)
        and (tier_filter is None or utils.str_or_none(item.get("tier")) == tier_filter)
    ]
    return http_utils.response(200, {"items": records, "nextToken": None})


def handle_update(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    auth.require_admin(caller)
    body = http_utils.require_json_body(event)

    item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    now = _now_utc()
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
    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    db.update_item(
        db_factory.tenants_table_name(),
        db_utils.tenant_key(tenant_id),
        expression,
        values,
        expression_attribute_names=names,
    )

    # Re-fetch for response
    updated_item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    old_tier = utils.str_or_none(item.get("tier"))
    new_tier = utils.str_or_none((updated_item or item).get("tier"))
    detail_type = "tenant.updated"
    detail: dict[str, Any] = {"tenantId": tenant_id, "actorSub": caller.sub}
    if old_tier != new_tier and new_tier is not None:
        detail_type = "tenant.tier_changed"
        detail["oldTier"] = old_tier
        detail["newTier"] = new_tier
    events.put_event(deps, detail_type=detail_type, detail=detail)
    return http_utils.response(
        200,
        {"tenant": serialization.serialize_tenant(updated_item or item)},
    )


def handle_delete(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    auth.require_admin(caller)

    item = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    now = _now_utc()
    # Soft delete: update status and set purge date
    updates = {
        "status": "deleted",
        "deletedAt": utils.iso(now),
        "purgeAtEpochSeconds": int(
            (now + timedelta(days=constants.DELETE_RETENTION_DAYS)).timestamp()
        ),
        "updatedAt": utils.iso(now),
    }

    expression, names, values = db_utils.build_update_expression(updates)
    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    db.update_item(
        db_factory.tenants_table_name(),
        db_utils.tenant_key(tenant_id),
        expression,
        values,
        expression_attribute_names=names,
    )

    deleted_item = dict(item)
    deleted_item.update(updates)
    events.put_event(
        deps,
        detail_type="tenant.deleted",
        detail={
            "tenantId": tenant_id,
            "actorSub": caller.sub,
            "retentionDays": constants.DELETE_RETENTION_DAYS,
            "purgeAtEpochSeconds": updates["purgeAtEpochSeconds"],
        },
    )
    return http_utils.response(200, {"tenant": serialization.serialize_tenant(deleted_item)})


def handle_tenant_provisioning_event(
    event: dict[str, Any],
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    detail = event.get("detail", {})
    tenant_id = utils.str_or_none(detail.get("tenantId"))
    app_id = utils.str_or_none(detail.get("appId"))
    if not tenant_id:
        raise ValueError("tenantId missing in provisioning event")

    detail_type = utils.str_or_none(event.get("detail-type"))
    if detail_type == "tenant.provisioned":
        status = "ready"
    elif detail_type == "tenant.provisioning_failed":
        status = "failed"
    else:
        status = utils.str_or_none(detail.get("status"))
    if status not in constants.TENANT_PROVISIONING_STATUSES:
        raise ValueError(f"Invalid provisioning status: {status}")

    now = _now_utc()
    updates: dict[str, Any] = {
        "provisioningStatus": status,
        "provisioningUpdatedAt": utils.iso(now),
        "updatedAt": utils.iso(now),
    }
    if status == "ready":
        updates["status"] = TenantStatus.ACTIVE.value
        execution_role_arn = detail.get("executionRoleArn") or detail.get("ExecutionRoleArn")
        memory_store_arn = detail.get("memoryStoreArn") or detail.get("MemoryStoreArn")
        if execution_role_arn is not None:
            updates["executionRoleArn"] = str(execution_role_arn)
        if memory_store_arn is not None:
            updates["memoryStoreArn"] = str(memory_store_arn)
    elif status == "failed":
        updates["provisioningError"] = str(
            detail.get("error") or detail.get("reason") or "Unknown error"
        )

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

    db = _db_for_tenant(tenant_id=tenant_id, caller=system_caller, app_id=app_id)
    expression, names, values = db_utils.build_update_expression(updates)
    db.update_item(
        db_factory.tenants_table_name(),
        db_utils.tenant_key(tenant_id),
        expression,
        values,
        expression_attribute_names=names,
    )

    updated_item = db_utils.read_tenant_record(
        tenant_id=tenant_id,
        caller=system_caller,
        app_id=app_id,
    )
    if updated_item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")
    return http_utils.response(200, {"tenant": serialization.serialize_tenant(updated_item)})


def handle_health(deps: models.TenantApiDependencies) -> dict[str, Any]:
    # Basic service health
    _ = deps
    return http_utils.response(
        200,
        {
            "status": "ok",
            "version": "pre-release",
            "runtimeRegion": os.environ.get("AWS_REGION", "unknown"),
            "timestamp": utils.iso(_now_utc()),
        },
    )


def handle_sessions(
    event: dict[str, Any],
    caller: models.CallerIdentity,
) -> dict[str, Any]:
    _ = caller
    query = event.get("queryStringParameters") or {}
    raw_limit = query.get("limit") if isinstance(query, dict) else None
    if raw_limit is not None:
        try:
            int(str(raw_limit))
        except (TypeError, ValueError):
            return http_utils.error(400, "BAD_REQUEST", "limit must be an integer")
    return http_utils.error(
        501,
        "NOT_IMPLEMENTED",
        "tenant-backed session tracking is not implemented",
    )


def handle_rotate_api_key(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not auth.can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Access denied")

    existing = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return http_utils.error(404, "NOT_FOUND", "Tenant not found")

    app_id = utils.str_or_none(existing.get("appId"))
    secret_arn = utils.str_or_none(existing.get("apiKeySecretArn"))
    if app_id is None or secret_arn is None:
        return http_utils.error(409, "CONFLICT", "Tenant is missing API key secret metadata")

    rotated_at = _now_utc()
    put_response = deps.secretsmanager.put_secret_value(
        SecretId=secret_arn,
        SecretString=json.dumps(
            {
                "tenantId": tenant_id,
                "appId": app_id,
                "apiKey": secrets.token_urlsafe(32),
                "rotatedAt": utils.iso(rotated_at),
            }
        ),
    )
    events.put_event(
        deps,
        detail_type="tenant.api_key_rotated",
        detail={
            "tenantId": tenant_id,
            "appId": app_id,
            "actorSub": caller.sub,
            "secretArn": secret_arn,
        },
    )
    return http_utils.response(
        200,
        {
            "tenantId": tenant_id,
            "apiKeySecretArn": secret_arn,
            "rotatedAt": utils.iso(rotated_at),
            "versionId": utils.str_or_none(put_response.get("VersionId")),
        },
    )


def handle_invite_user(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not auth.can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Access denied")

    existing = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return http_utils.error(404, "NOT_FOUND", "Tenant not found")

    body = http_utils.require_json_body(event)
    email = utils.str_or_none(body.get("email"))
    if email is None or "@" not in email:
        raise ValueError("email is required and must be a valid email address")
    role = lifecycle_logic.normalize_tenant_invite_role(body.get("role"))
    invite = {
        "inviteId": f"invite-{secrets.token_hex(8)}",
        "tenantId": tenant_id,
        "email": email.lower(),
        "role": role,
        "status": "pending",
        "expiresAt": utils.iso(_now_utc() + timedelta(days=7)),
    }
    events.put_event(
        deps,
        detail_type="tenant.user_invited",
        detail={
            **invite,
            "actorSub": caller.sub,
            "appId": utils.str_or_none(existing.get("appId")),
        },
    )
    return http_utils.response(202, {"invite": invite})


def _invocation_timestamp(item: dict[str, Any]) -> str | None:
    timestamp = utils.str_or_none(item.get("timestamp"))
    if timestamp is not None:
        return timestamp
    sort_key = utils.str_or_none(item.get("SK"))
    if sort_key is None or not sort_key.startswith("INV#"):
        return None
    parts = sort_key.split("#", 2)
    if len(parts) < 3:
        return None
    return utils.str_or_none(parts[1])


def _audit_export_sk_condition(
    *,
    start_at: Any,
    end_at: Any,
) -> Any:
    start_text = f"INV#{utils.iso(start_at)}" if start_at is not None else None
    end_text = f"INV#{utils.iso(end_at)}~" if end_at is not None else None
    if start_text and end_text:
        return Key("SK").between(start_text, end_text)
    if start_text:
        return Key("SK").gte(start_text)
    if end_text:
        return Key("SK").lte(end_text)
    return None


def _collect_audit_export_records(
    *,
    tenant_id: str,
    caller: models.CallerIdentity,
    app_id: str | None,
    start_at: Any,
    end_at: Any,
) -> list[dict[str, Any]]:
    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    last_evaluated_key: dict[str, Any] | None = None
    items: list[dict[str, Any]] = []
    sk_condition = _audit_export_sk_condition(start_at=start_at, end_at=end_at)
    start_text = utils.iso(start_at) if start_at is not None else None
    end_text = utils.iso(end_at) if end_at is not None else None

    while True:
        page = db.query(
            db_factory.invocations_table_name(),
            sk_condition=sk_condition,
            limit=constants.AUDIT_EXPORT_PAGE_SIZE,
            exclusive_start_key=last_evaluated_key,
        )
        for item in page.items:
            item_timestamp = _invocation_timestamp(item)
            if item_timestamp is None:
                continue
            if start_text is not None and item_timestamp < start_text:
                continue
            if end_text is not None and item_timestamp > end_text:
                continue
            items.append(item)
        last_evaluated_key = page.last_evaluated_key
        if last_evaluated_key is None:
            return items


def _format_export_timestamp(value: Any) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def _audit_export_url_expiry_seconds() -> int:
    raw = os.environ.get("AUDIT_EXPORT_URL_EXPIRY_SECONDS")
    return utils.coerce_positive_int(raw, default=constants.AUDIT_EXPORT_URL_EXPIRY_SECONDS)


def _audit_export_key(tenant_id: str, generated_at: Any) -> str:
    timestamp = _format_export_timestamp(generated_at)
    nonce = secrets.token_hex(8)
    return (
        f"tenants/{tenant_id}/{constants.AUDIT_EXPORT_PREFIX}/audit-export-{timestamp}-{nonce}.json"
    )


def _build_audit_export_payload(
    *,
    tenant_id: str,
    generated_at: Any,
    start_at: Any,
    end_at: Any,
    records: list[dict[str, Any]],
) -> bytes:
    payload = {
        "tenantId": tenant_id,
        "generatedAt": utils.iso(generated_at),
        "windowStart": utils.iso(start_at) if start_at is not None else None,
        "windowEnd": utils.iso(end_at) if end_at is not None else None,
        "recordCount": len(records),
        "records": records,
    }
    return json.dumps(payload, default=utils.json_default).encode("utf-8")


def handle_audit_export(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    auth.require_admin(caller)
    existing = db_utils.read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return http_utils.error(404, "NOT_FOUND", "Tenant not found")

    query = event.get("queryStringParameters") or {}
    start_at = validation.parse_optional_utc_timestamp(query.get("start"), field="start")
    end_at = validation.parse_optional_utc_timestamp(query.get("end"), field="end")
    if start_at is not None and end_at is not None and start_at > end_at:
        raise ValueError("start must be less than or equal to end")

    bucket = utils.str_or_none(os.environ.get(constants.AUDIT_EXPORT_BUCKET_ENV))
    if bucket is None:
        return http_utils.error(500, "INTERNAL_ERROR", "Audit export bucket is not configured")

    app_id = utils.str_or_none(existing.get("appId"))
    records = _collect_audit_export_records(
        tenant_id=tenant_id,
        caller=caller,
        app_id=app_id,
        start_at=start_at,
        end_at=end_at,
    )
    generated_at = _now_utc()
    object_key = _audit_export_key(tenant_id, generated_at)
    payload = _build_audit_export_payload(
        tenant_id=tenant_id,
        generated_at=generated_at,
        start_at=start_at,
        end_at=end_at,
        records=records,
    )

    try:
        tenant_s3 = db_factory.s3_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
        tenant_s3.put_object(
            bucket,
            object_key,
            payload,
            ContentType="application/json",
        )
        expires_in = _audit_export_url_expiry_seconds()
        download_url = tenant_s3.generate_presigned_url(
            bucket,
            object_key,
            expires_in=expires_in,
        )
    except Exception:
        return http_utils.error(500, "INTERNAL_ERROR", "Failed to generate audit export")

    expires_at = generated_at + timedelta(seconds=_audit_export_url_expiry_seconds())
    return http_utils.response(
        200,
        {
            "tenantId": tenant_id,
            "downloadUrl": download_url,
            "expiresAt": utils.iso(expires_at),
        },
    )


def handle_list_invites(
    caller: models.CallerIdentity,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not auth.can_read_tenant(caller, tenant_id) or not auth.can_manage_tenant_self_service(
        caller, tenant_id
    ):
        raise PermissionError("Access denied")

    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    item = db.get_item(db_factory.tenants_table_name(), db_utils.tenant_key(tenant_id))
    if item is None:
        return http_utils.error(404, "NOT_FOUND", f"Tenant '{tenant_id}' not found")

    results = db.query(
        db_factory.tenants_table_name(),
        sk_condition=Key("SK").begins_with("INVITE#"),
    )

    invites = [
        {
            "inviteId": str(invite.get("inviteId", "")),
            "tenantId": tenant_id,
            "email": str(invite.get("email", "")),
            "role": str(invite.get("role", "Agent.Invoke")),
            "status": str(invite.get("status", "")),
            "expiresAt": invite.get("expiresAt"),
        }
        for invite in results.items
    ]
    return http_utils.response(200, {"items": invites})


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
    if path == "/v1/tenants" and method == "GET":
        return handle_list_tenants(event, caller, deps)
    if tenant_id:
        if path == f"/v1/tenants/{tenant_id}" and method == "GET":
            return handle_read(caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}" and method == "PATCH":
            return handle_update(event, caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}" and method == "PUT":
            return handle_update(event, caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}" and method == "DELETE":
            return handle_delete(caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}/api-key/rotate" and method == "POST":
            return handle_rotate_api_key(caller, deps, tenant_id=tenant_id)
        if path == f"/v1/tenants/{tenant_id}/users/invite" and method == "POST":
            return handle_invite_user(event, caller, deps, tenant_id=tenant_id)
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

    if caller.tenant_id:
        if path == "/v1/webhooks":
            try:
                from src.tenant_api import webhook_registry
            except (ImportError, ValueError):
                from . import webhook_registry
            return webhook_registry.dispatch_routes(
                f"/v1/tenants/{caller.tenant_id}/webhooks",
                method,
                event,
                caller,
                deps,
                caller.tenant_id,
            )
        if path.startswith("/v1/webhooks/"):
            webhook_id = path.removeprefix("/v1/webhooks/")
            try:
                from src.tenant_api import webhook_registry
            except (ImportError, ValueError):
                from . import webhook_registry
            return webhook_registry.dispatch_routes(
                f"/v1/tenants/{caller.tenant_id}/webhooks/{webhook_id}",
                method,
                event,
                caller,
                deps,
                caller.tenant_id,
            )

    return None
