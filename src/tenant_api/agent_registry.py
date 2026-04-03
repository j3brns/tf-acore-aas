from __future__ import annotations

import re
from typing import Any

from data_access.models import REGISTERABLE_AGENT_STATUSES, AgentStatus, normalize_agent_status

try:
    from . import (
        agent_logic,
        auth,
        db_factory,
        db_utils,
        events,
        http_utils,
        models,
        utils,
        validation,
    )
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import (
        agent_logic,
        auth,
        db_factory,
        db_utils,
        events,
        http_utils,
        models,
        utils,
        validation,
    )


_REGISTER_MUTABLE_FIELDS = frozenset(
    {
        "displayName",
        "ownerTeam",
        "tierMinimum",
        "invocationMode",
        "streamingEnabled",
        "layerHash",
        "layerS3Key",
        "scriptS3Key",
        "estimatedDurationSeconds",
        "runtimeArn",
        "agUiEnabled",
        "agUiTransport",
        "agUiEndpoint",
    }
)


def _requires_zip_layer_metadata(item: dict[str, Any]) -> bool:
    runtime_arn = str(item.get("runtime_arn", "")).strip()
    return runtime_arn == ""


def _validate_layer_metadata_invariants(item: dict[str, Any]) -> None:
    if not _requires_zip_layer_metadata(item):
        return

    missing_fields: list[str] = []
    if str(item.get("layer_hash", "")).strip() == "":
        missing_fields.append("layerHash")
    if str(item.get("layer_s3_key", "")).strip() == "":
        missing_fields.append("layerS3Key")

    if missing_fields:
        joined = ", ".join(missing_fields)
        raise ValueError(f"Zip-agent registration requires non-empty {joined}")


def handle_list_agents(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    _ = event
    _ = deps
    auth.require_admin(caller)
    db = db_factory.control_plane_db(caller)
    items = db.scan_all(db_factory.agents_table_name())
    return http_utils.response(200, {"items": items})


def handle_register_agent(
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any]:
    auth.require_admin(caller)
    auth.require_platform_actor(caller)
    body = http_utils.require_json_body(event)

    agent_name = validation.canonical_tenant_id(body.get("agentName"))
    version = str(body.get("version", "")).strip()
    if not version:
        raise ValueError("version is required")

    status_raw = body.get("status")
    initial_status = (
        agent_logic.normalize_agent_status_val(status_raw) if status_raw else AgentStatus.BUILT
    )

    if initial_status not in REGISTERABLE_AGENT_STATUSES:
        allowed = ", ".join(s.value for s in sorted(REGISTERABLE_AGENT_STATUSES))
        raise ValueError(f"Initial status for registration must be one of: {allowed}")

    db = db_factory.control_plane_db(caller)
    now = utils.now_utc()
    now_iso = utils.iso(now)

    item = {
        "PK": f"AGENT#{agent_name}",
        "SK": f"VERSION#{version}",
        "agent_name": agent_name,
        "version": version,
        "status": initial_status.value,
        "owner_team": str(body.get("ownerTeam", "unknown")).strip(),
        "tier_minimum": str(body.get("tierMinimum", "basic")).strip(),
        "invocation_mode": str(body.get("invocationMode", "sync")).strip(),
        "streaming_enabled": bool(body.get("streamingEnabled", False)),
        "layer_hash": str(body.get("layerHash", "")).strip(),
        "layer_s3_key": str(body.get("layerS3Key", "")).strip(),
        "script_s3_key": str(body.get("script_s3_key", "")).strip(),
        "deployed_at": now_iso,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    if "displayName" in body:
        item["display_name"] = str(body["displayName"]).strip()
    if "estimatedDurationSeconds" in body:
        item["estimated_duration_seconds"] = int(body["estimatedDurationSeconds"])
    if "runtimeArn" in body:
        item["runtime_arn"] = str(body["runtimeArn"]).strip()

    # AG-UI Config
    ag_ui = {
        "enabled": bool(body.get("agUiEnabled", False)),
        "transport": str(body.get("agUiTransport", "sse")).strip(),
        "endpoint": str(body.get("agUiEndpoint", "")).strip() or None,
    }
    item["ag_ui"] = ag_ui

    _validate_layer_metadata_invariants(item)

    db.put_item(db_factory.agents_table_name(), item)

    return http_utils.response(
        201, {"agentName": agent_name, "version": version, "status": item["status"]}
    )


def handle_promote_agent(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    agent_name: str,
    version: str,
) -> dict[str, Any]:
    return _update_agent_status(
        caller, deps, agent_name=agent_name, version=version, target_status=AgentStatus.PROMOTED
    )


def handle_rollback_agent(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    agent_name: str,
    version: str,
) -> dict[str, Any]:
    return _update_agent_status(
        caller, deps, agent_name=agent_name, version=version, target_status=AgentStatus.ROLLED_BACK
    )


def _update_agent_status(
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
    *,
    agent_name: str,
    version: str,
    target_status: AgentStatus,
) -> dict[str, Any]:
    auth.require_admin(caller)
    auth.require_platform_actor(caller)

    db = db_factory.control_plane_db(caller)
    table_name = db_factory.agents_table_name()
    key = {"PK": f"AGENT#{agent_name}", "SK": f"VERSION#{version}"}

    existing = db.get_item(table_name, key)
    if not existing:
        return http_utils.error(404, "NOT_FOUND", f"Agent version {agent_name}:{version} not found")

    current_status = normalize_agent_status(existing.get("status"))
    agent_logic.validate_agent_status_transition(current_status, target_status)

    now = utils.now_utc()
    now_iso = utils.iso(now)

    updates: dict[str, Any] = {
        "status": target_status.value,
        "updatedAt": now_iso,
    }
    if target_status == AgentStatus.PROMOTED:
        updates["approved_at"] = now_iso
        updates["approved_by"] = caller.sub
    elif target_status == AgentStatus.ROLLED_BACK:
        updates["rolled_back_at"] = now_iso
        updates["rolled_back_by"] = caller.sub

    expression, names, values = db_utils.build_update_expression(updates)
    db.update_item(
        table_name,
        {"PK": f"AGENT#{agent_name}", "SK": f"VERSION#{version}"},
        expression,
        values,
        expression_attribute_names=names,
    )

    # Emit lifecycle event
    event_detail = agent_logic.build_agent_release_lifecycle_event_detail(
        caller=caller,
        agent_name=agent_name,
        version=version,
        previous_status=current_status,
        new_status=target_status,
        occurred_at=now_iso,
        approved_by=updates.get("approved_by"),
        approved_at=updates.get("approved_at"),
        release_notes=existing.get("release_notes"),
        evaluation_score=existing.get("evaluation_score"),
        evaluation_report_url=existing.get("evaluation_report_url"),
        rolled_back_by=updates.get("rolled_back_by"),
        rolled_back_at=updates.get("rolled_back_at"),
    )

    detail_type = agent_logic.agent_event_detail_type(target_status)
    if detail_type:
        events.put_event(deps, detail_type=detail_type, detail=event_detail)

    return http_utils.response(
        200, {"agentName": agent_name, "version": version, "status": target_status.value}
    )


def dispatch_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: models.CallerIdentity,
    deps: models.TenantApiDependencies,
) -> dict[str, Any] | None:
    # /v1/platform/agents
    if path == "/v1/platform/agents" and method == "GET":
        return handle_list_agents(event, caller, deps)
    if path == "/v1/platform/agents" and method == "POST":
        return handle_register_agent(event, caller, deps)

    # /v1/platform/agents/{name}/versions/{version}/promote
    promote_match = re.match(r"^/v1/platform/agents/([^/]+)/versions/([^/]+)/promote$", path)
    if promote_match and method == "POST":
        return handle_promote_agent(
            caller, deps, agent_name=promote_match.group(1), version=promote_match.group(2)
        )

    # /v1/platform/agents/{name}/versions/{version}/rollback
    rollback_match = re.match(r"^/v1/platform/agents/([^/]+)/versions/([^/]+)/rollback$", path)
    if rollback_match and method == "POST":
        return handle_rollback_agent(
            caller, deps, agent_name=rollback_match.group(1), version=rollback_match.group(2)
        )

    return None
