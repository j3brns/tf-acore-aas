from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError
from data_access.models import REGISTERABLE_AGENT_STATUSES, AgentStatus, normalize_agent_status

try:
    import handler as shared
except ImportError:  # pragma: no cover - local package import path
    from src.tenant_api import handler as shared


def handle_list_agents(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    _ = event
    _ = deps
    shared._require_admin(caller)
    db = shared._control_plane_db(caller)
    items = db.scan_all(shared._agents_table_name())
    return shared._response(200, {"items": items})


def handle_register_agent(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    shared._require_admin(caller)
    body = shared._require_json_body(event)

    agent_name = shared._str_or_none(body.get("agentName"))
    version = shared._str_or_none(body.get("version"))

    if not agent_name or not version:
        raise ValueError("agentName and version are required")

    status = (
        AgentStatus.BUILT
        if body.get("status") is None
        else shared._normalize_agent_status(body.get("status"))
    )
    if status not in REGISTERABLE_AGENT_STATUSES:
        raise ValueError("New agent versions may only be registered as built")

    item = {
        "PK": f"AGENT#{agent_name}",
        "SK": f"VERSION#{version}",
        "agent_name": agent_name,
        "version": version,
        "owner_team": str(body.get("ownerTeam", "unknown")),
        "tier_minimum": str(body.get("tierMinimum", "basic")),
        "layer_hash": str(body.get("layerHash", "")),
        "layer_s3_key": str(body.get("layerS3Key", "")),
        "script_s3_key": str(body.get("scriptS3Key", "")),
        "deployed_at": str(body.get("deployedAt", shared._iso(shared._now_utc()))),
        "invocation_mode": str(body.get("invocationMode", "sync")),
        "streaming_enabled": bool(body.get("streamingEnabled", False)),
        "status": status.value,
        "runtime_arn": shared._str_or_none(body.get("runtimeArn")),
        "estimated_duration_seconds": shared._coerce_positive_int(
            body.get("estimatedDurationSeconds"), default=0
        ),
        "commit_sha": shared._str_or_none(body.get("commitSha")),
        "pipeline_url": shared._str_or_none(body.get("pipelineUrl")),
        "job_id": shared._str_or_none(body.get("jobId")),
    }
    if status in {AgentStatus.APPROVED, AgentStatus.PROMOTED}:
        item["approved_by"] = caller.sub
        item["approved_at"] = shared._iso(shared._now_utc())
    if body.get("releaseNotes"):
        item["release_notes"] = str(body["releaseNotes"])

    _ = deps
    db = shared._control_plane_db(caller)
    try:
        db.put_item(
            shared._agents_table_name(),
            item,
            condition_expression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return shared._error(
                409,
                "CONFLICT",
                f"Agent version {agent_name}:{version} already registered",
            )
        raise

    return shared._response(
        201,
        {"status": "registered", "agentName": agent_name, "version": version},
    )


def handle_update_agent_version(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    agent_name: str,
    version: str,
) -> dict[str, Any]:
    shared._require_admin(caller)
    body = shared._require_json_body(event)

    new_status = shared._str_or_none(body.get("status"))
    if not new_status:
        raise ValueError("status is required")

    new_agent_status = shared._normalize_agent_status(new_status)
    db = shared._control_plane_db(caller)
    key = {"PK": f"AGENT#{agent_name}", "SK": f"VERSION#{version}"}

    existing = db.get_item(shared._agents_table_name(), key)
    if not existing:
        return shared._error(404, "NOT_FOUND", f"Agent version {agent_name}:{version} not found")

    current_status = normalize_agent_status(existing.get("status"), default=AgentStatus.PROMOTED)
    shared._validate_agent_status_transition(current_status, new_agent_status)

    updated_at = shared._iso(shared._now_utc())
    attrs: dict[str, Any] = {
        "status": new_agent_status.value,
        "updated_at": updated_at,
    }
    if new_agent_status in {AgentStatus.APPROVED, AgentStatus.PROMOTED}:
        attrs["approved_by"] = caller.sub
        attrs["approved_at"] = updated_at
    if new_agent_status is AgentStatus.ROLLED_BACK:
        attrs["rolled_back_by"] = caller.sub
        attrs["rolled_back_at"] = updated_at
    if body.get("releaseNotes") is not None:
        attrs["release_notes"] = str(body["releaseNotes"])
    if body.get("evaluationScore") is not None:
        attrs["evaluation_score"] = float(body["evaluationScore"])
    if body.get("evaluationReportUrl") is not None:
        attrs["evaluation_report_url"] = str(body["evaluationReportUrl"])

    update_expression, expr_names, expr_values = shared._build_update_expression(attrs)
    db.update_item(
        shared._agents_table_name(),
        key=key,
        update_expression=update_expression,
        expression_attribute_values=expr_values,
        expression_attribute_names=expr_names,
        condition_expression="attribute_exists(PK) AND attribute_exists(SK)",
    )

    detail_type = shared._agent_event_detail_type(new_agent_status)
    if detail_type is not None and new_agent_status != current_status:
        approved_by = shared._str_or_none(attrs.get("approved_by") or existing.get("approved_by"))
        approved_at = shared._str_or_none(attrs.get("approved_at") or existing.get("approved_at"))
        release_notes = shared._str_or_none(attrs.get("release_notes"))
        evaluation_score_raw = attrs.get("evaluation_score")
        evaluation_score = float(evaluation_score_raw) if evaluation_score_raw is not None else None
        evaluation_report_url = shared._str_or_none(attrs.get("evaluation_report_url"))
        rolled_back_by = shared._str_or_none(attrs.get("rolled_back_by"))
        rolled_back_at = shared._str_or_none(attrs.get("rolled_back_at"))
        shared._put_event(
            deps,
            detail_type=detail_type,
            detail=shared._build_agent_release_lifecycle_event_detail(
                caller=caller,
                agent_name=agent_name,
                version=version,
                previous_status=current_status,
                new_status=new_agent_status,
                occurred_at=updated_at,
                approved_by=approved_by,
                approved_at=approved_at,
                release_notes=release_notes,
                evaluation_score=evaluation_score,
                evaluation_report_url=evaluation_report_url,
                rolled_back_by=rolled_back_by,
                rolled_back_at=rolled_back_at,
            ),
        )

    return shared._response(
        200,
        {
            "status": "updated",
            "agentName": agent_name,
            "version": version,
            "newStatus": new_agent_status.value,
        },
    )


def dispatch_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any] | None:
    if path == "/v1/platform/agents" and method == "GET":
        return handle_list_agents(event, caller, deps)
    if path == "/v1/platform/agents" and method == "POST":
        return handle_register_agent(event, caller, deps)

    if path.startswith("/v1/platform/agents/") and method == "PATCH":
        parts = path.split("/")
        if len(parts) == 7 and parts[5].lower() == "versions":
            agent_name = parts[4]
            version = parts[6]
            return handle_update_agent_version(
                event, caller, deps, agent_name=agent_name, version=version
            )

    return None
