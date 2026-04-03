from __future__ import annotations

from boto3.dynamodb.conditions import Key

try:
    from . import auth, db_factory, db_utils, http_utils, models
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import auth, db_factory, db_utils, http_utils, models


def handle_list_invites(
    caller: models.CallerIdentity,
    *,
    tenant_id: str,
) -> dict[str, object]:
    if not auth.can_read_tenant(caller, tenant_id) or not auth.can_manage_tenant_self_service(
        caller, tenant_id
    ):
        raise PermissionError("Access denied")

    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=None)
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
