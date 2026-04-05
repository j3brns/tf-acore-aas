from __future__ import annotations

import re
import secrets
import sys
import urllib.parse
import uuid
from typing import Any

from boto3.dynamodb.conditions import Key

try:
    from . import auth, db_factory, http_utils, models, utils
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import (
        auth,
        db_factory,
        http_utils,
        models,
        utils,
    )


def _shared_handler() -> Any | None:
    return sys.modules.get("src.tenant_api.handler") or sys.modules.get("handler")


def _db_for_tenant(*, tenant_id: str, caller: models.CallerIdentity, app_id: str | None):
    shared = _shared_handler()
    if shared is not None and hasattr(shared, "_db_for_tenant"):
        return shared._db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    return db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)


def _now_utc():
    shared = _shared_handler()
    if shared is not None and hasattr(shared, "_now_utc"):
        return shared._now_utc()
    return utils.now_utc()


def handle_list_webhooks(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    _ = deps
    if not auth.can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    result = db.query(
        db_factory.tenants_table_name(),
        sk_condition=Key("SK").begins_with("WEBHOOK#"),
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

    return http_utils.response(200, {"items": items})


def handle_register_webhook(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    _ = deps
    if not auth.can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    body = http_utils.require_json_body(event)
    callback_url = utils.str_or_none(body.get("callbackUrl"))
    if callback_url is None:
        raise ValueError("callbackUrl is required")

    parsed_url = urllib.parse.urlparse(callback_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return http_utils.error(422, "UNPROCESSABLE_ENTITY", "callbackUrl must be a valid URL")

    events_raw = body.get("events")
    if not isinstance(events_raw, list) or not events_raw:
        raise ValueError("events must be a non-empty array")

    valid_events = {"job.completed", "job.failed"}
    normalized_events: list[str] = []
    seen_events: set[str] = set()
    for raw_event in events_raw:
        event_name = utils.str_or_none(raw_event)
        if event_name is None:
            return http_utils.error(
                422,
                "UNPROCESSABLE_ENTITY",
                "events must contain non-empty values",
            )
        if event_name not in valid_events:
            return http_utils.error(
                422, "UNPROCESSABLE_ENTITY", f"Unsupported webhook event '{event_name}'"
            )
        if event_name in seen_events:
            raise ValueError("events must not contain duplicate values")
        seen_events.add(event_name)
        normalized_events.append(event_name)

    description = utils.str_or_none(body.get("description"))
    if description and len(description) > 256:
        return http_utils.error(
            422, "UNPROCESSABLE_ENTITY", "description must be 256 characters or fewer"
        )

    webhook_id = str(uuid.uuid4())
    now = _now_utc()
    webhook_secret = secrets.token_urlsafe(32)

    webhook = {
        "PK": f"TENANT#{tenant_id}",
        "SK": f"WEBHOOK#{webhook_id}",
        "webhook_id": webhook_id,
        "tenant_id": tenant_id,
        "callback_url": callback_url,
        "events": normalized_events,
        "status": "active",
        "signature_secret": webhook_secret,
        "description": description,
        "created_at": utils.iso(now),
        "updated_at": utils.iso(now),
        "signature_header": "X-Platform-Signature",
        "signature_algorithm": "HMAC-SHA256",
    }

    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    db.put_item(db_factory.tenants_table_name(), webhook)

    return http_utils.response(
        201,
        {
            "webhookId": webhook_id,
            "callbackUrl": callback_url,
            "events": normalized_events,
            "status": "active",
            "createdAt": utils.iso(now),
            "signatureHeader": "X-Platform-Signature",
            "signatureAlgorithm": "HMAC-SHA256",
        },
    )


def handle_delete_webhook(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    tenant_id: str,
    webhook_id: str,
) -> dict[str, Any]:
    _ = deps
    if not auth.can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    db = _db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
    key = {"PK": f"TENANT#{tenant_id}", "SK": f"WEBHOOK#{webhook_id}"}

    # Verify existence and ownership
    existing = db.get_item(db_factory.tenants_table_name(), key)
    if existing is None:
        return http_utils.error(404, "NOT_FOUND", f"Webhook '{webhook_id}' not found")

    db.delete_item(db_factory.tenants_table_name(), key)
    return http_utils.response(204, {})


def dispatch_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    if tenant_id is None:
        tenant_id = caller.tenant_id

    # /v1/tenants/{tenant_id}/webhooks
    if tenant_id and path in {f"/v1/tenants/{tenant_id}/webhooks", "/v1/webhooks"}:
        if method == "GET":
            return handle_list_webhooks(caller, deps, tenant_id=tenant_id)
        if method == "POST":
            return handle_register_webhook(event, caller, deps, tenant_id=tenant_id)

    # /v1/tenants/{tenant_id}/webhooks/{webhook_id}
    if tenant_id:
        match = re.match(rf"^/v1/tenants/{tenant_id}/webhooks/([^/]+)$", path)
        if match is None:
            match = re.match(r"^/v1/webhooks/([^/]+)$", path)
        if match and method == "DELETE":
            return handle_delete_webhook(
                caller, deps, tenant_id=tenant_id, webhook_id=match.group(1)
            )

    return None
