from __future__ import annotations

import sys
from decimal import Decimal
from typing import Any

from src.tenant_api.db_factory import control_plane_db, tenants_table_name
from src.tenant_api.db_factory import (
    ops_locks_table_name as _ops_locks_table_name,
)
from src.tenant_api.models import CallerIdentity, TenantApiDependencies


def _shared_handler() -> Any | None:
    return sys.modules.get("src.tenant_api.handler") or sys.modules.get("handler")


def db_for_tenant(*, tenant_id: str, caller: CallerIdentity, app_id: str | None = None):
    shared = _shared_handler()
    if shared is not None and hasattr(shared, "_db_for_tenant"):
        return shared._db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    from src.tenant_api.db_factory import db_for_tenant as _db_for_tenant_impl

    return _db_for_tenant_impl(tenant_id=tenant_id, caller=caller, app_id=app_id)


def tenant_pk(tenant_id: str) -> str:
    return f"TENANT#{tenant_id}"


def tenant_key(tenant_id: str) -> dict[str, str]:
    return {"PK": tenant_pk(tenant_id), "SK": "METADATA"}


def ddb_value(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def read_tenant_record(
    *,
    tenant_id: str,
    caller: CallerIdentity,
    app_id: str | None = None,
) -> dict[str, Any] | None:
    db = db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    return db.get_item(tenants_table_name(), tenant_key(tenant_id))


def build_update_expression(
    attributes: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, Any]]:
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    set_parts: list[str] = []
    for idx, (field, raw_value) in enumerate(attributes.items(), start=1):
        name_key = f"#n{idx}"
        value_key = f":v{idx}"
        names[name_key] = field
        values[value_key] = ddb_value(raw_value)
        set_parts.append(f"{name_key} = {value_key}")
    return "SET " + ", ".join(set_parts), names, values


def read_failover_lock_record(
    caller: CallerIdentity, deps: TenantApiDependencies
) -> dict[str, Any] | None:
    import os

    from src.tenant_api.constants import DEFAULT_FAILOVER_LOCK_NAME, FAILOVER_LOCK_NAME_ENV

    _ = deps
    db = control_plane_db(caller)
    lock_name = os.environ.get(FAILOVER_LOCK_NAME_ENV, DEFAULT_FAILOVER_LOCK_NAME)
    return db.get_item(
        _ops_locks_table_name(),
        {"PK": f"LOCK#{lock_name}", "SK": "METADATA"},
    )
