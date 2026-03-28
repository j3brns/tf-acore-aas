from __future__ import annotations

from typing import Any

try:
    import handler as shared
except ImportError:  # pragma: no cover - local package import path
    from src.tenant_api import handler as shared


def handle_list_webhooks(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    _ = deps
    if not shared._can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    db = shared._db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    result = db.query(
        shared._tenants_table_name(),
        sk_condition=shared.Key("SK").begins_with("WEBHOOK#"),
    )

    items = []
    for item in result.items:
        items.append(
            {
                "webhookId": item.get("webhook_id"),
                "callbackUrl": item.get("callback_url"),
                "events": item.get("events"),
                "status": item.get("status"),
                "description": item.get("description"),
                "createdAt": item.get("created_at"),
                "updatedAt": item.get("updated_at"),
                "signatureHeader": item.get("signature_header", "X-Platform-Signature"),
                "signatureAlgorithm": item.get("signature_algorithm", "HMAC-SHA256"),
            }
        )

    return shared._response(200, {"items": items})


def handle_register_webhook(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not shared._can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    body = shared._require_json_body(event)
    callback_url = shared._str_or_none(body.get("callbackUrl"))
    if callback_url is None:
        raise ValueError("callbackUrl is required")

    parsed_url = shared.urllib.parse.urlparse(callback_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return shared._error(422, "UNPROCESSABLE_ENTITY", "callbackUrl must be a valid URL")

    events_raw = body.get("events")
    if not isinstance(events_raw, list) or not events_raw:
        raise ValueError("events must be a non-empty array")

    valid_events = {"job.completed", "job.failed"}
    normalized_events: list[str] = []
    seen_events: set[str] = set()
    for raw_event in events_raw:
        event_name = shared._str_or_none(raw_event)
        if event_name is None:
            return shared._error(
                422,
                "UNPROCESSABLE_ENTITY",
                "events must contain non-empty values",
            )
        if event_name not in valid_events:
            return shared._error(
                422, "UNPROCESSABLE_ENTITY", f"Unsupported webhook event '{event_name}'"
            )
        if event_name in seen_events:
            raise ValueError("events must not contain duplicate values")
        seen_events.add(event_name)
        normalized_events.append(event_name)

    description = shared._str_or_none(body.get("description"))
    if description and len(description) > 256:
        return shared._error(
            422, "UNPROCESSABLE_ENTITY", "description must be 256 characters or fewer"
        )

    webhook_id = str(shared.uuid.uuid4())
    now = shared._now_utc()
    webhook_secret = shared.secrets.token_urlsafe(32)

    webhook = {
        "PK": f"TENANT#{tenant_id}",
        "SK": f"WEBHOOK#{webhook_id}",
        "webhook_id": webhook_id,
        "tenant_id": tenant_id,
        "callback_url": callback_url,
        "events": normalized_events,
        "status": "active",
        "description": description,
        "created_at": shared._iso(now),
        "updated_at": shared._iso(now),
        "signature_secret": webhook_secret,
        "signature_header": "X-Platform-Signature",
        "signature_algorithm": "HMAC-SHA256",
        "record_type": "webhook_registration",
    }

    db = shared._db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    db.put_item(shared._tenants_table_name(), webhook)

    shared._put_event(
        deps,
        detail_type="tenant.webhook_registered",
        detail={**webhook, "actorSub": caller.sub},
    )

    return shared._response(
        201,
        {
            "webhookId": webhook_id,
            "callbackUrl": callback_url,
            "events": normalized_events,
            "createdAt": shared._iso(now),
            "signatureHeader": "X-Platform-Signature",
            "signatureAlgorithm": "HMAC-SHA256",
        },
    )


def handle_delete_webhook(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    *,
    tenant_id: str,
    webhook_id: str,
) -> dict[str, Any]:
    if not shared._can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    db = shared._db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    key = {"PK": f"TENANT#{tenant_id}", "SK": f"WEBHOOK#{webhook_id}"}
    existing = db.get_item(shared._tenants_table_name(), key)
    if existing is None:
        return shared._error(404, "NOT_FOUND", "Webhook not found")

    db.delete_item(shared._tenants_table_name(), key)
    shared._put_event(
        deps,
        detail_type="tenant.webhook_deleted",
        detail={"tenantId": tenant_id, "webhookId": webhook_id, "actorSub": caller.sub},
    )
    return shared._response(204, {})


def dispatch_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any] | None:
    path_lower = path.lower()
    if path_lower == "/v1/webhooks":
        if caller.tenant_id is None:
            return shared._error(400, "BAD_REQUEST", "tenant context required")
        if method == "GET":
            return handle_list_webhooks(caller, deps, tenant_id=caller.tenant_id)
        if method == "POST":
            return handle_register_webhook(event, caller, deps, tenant_id=caller.tenant_id)

    if path_lower.startswith("/v1/webhooks/") and method == "DELETE":
        parts = path.split("/")
        if len(parts) == 4:
            if caller.tenant_id is None:
                return shared._error(400, "BAD_REQUEST", "tenant context required")
            return handle_delete_webhook(
                caller, deps, tenant_id=caller.tenant_id, webhook_id=parts[3]
            )

    return None
