from __future__ import annotations

from typing import Any

try:
    import handler as shared
except ImportError:  # pragma: no cover - local package import path
    from src.tenant_api import handler as shared


def handle_create(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    shared._require_admin(caller)
    body = shared._require_json_body(event)
    required = ["tenantId", "appId", "displayName", "tier", "ownerEmail", "ownerTeam", "accountId"]
    missing = [field for field in required if shared._str_or_none(body.get(field)) is None]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")

    tenant_id = shared._canonical_tenant_id(body["tenantId"])
    app_id = str(body["appId"]).strip()
    now = shared._now_utc()
    tier = shared._normalize_tier(body.get("tier"))

    if shared._read_tenant_record(tenant_id=tenant_id, caller=caller, app_id=app_id) is not None:
        return shared._error(409, "CONFLICT", "Tenant already exists")

    memory_info = deps.memory_provisioner.provision(tenant_id=tenant_id, app_id=app_id) or {}
    api_key_secret_arn = shared._create_api_key_secret(deps, tenant_id=tenant_id, app_id=app_id)

    attributes: dict[str, Any] = {
        "tenantId": tenant_id,
        "appId": app_id,
        "displayName": str(body["displayName"]).strip(),
        "tier": tier,
        "status": shared.TenantStatus.ACTIVE.value,
        "createdAt": shared._iso(now),
        "updatedAt": shared._iso(now),
        "provisioningStatus": "pending",
        "provisioningUpdatedAt": shared._iso(now),
        "ownerEmail": str(body["ownerEmail"]).strip(),
        "ownerTeam": str(body["ownerTeam"]).strip(),
        "accountId": str(body["accountId"]).strip(),
        "apiKeySecretArn": api_key_secret_arn,
    }
    if body.get("monthlyBudgetUsd") is not None:
        attributes["monthlyBudgetUsd"] = shared._as_float(
            body["monthlyBudgetUsd"],
            field="monthlyBudgetUsd",
        )

    for field in ("runtimeRegion", "fallbackRegion", "executionRoleArn"):
        text = shared._str_or_none(body.get(field))
        if text is not None:
            attributes[field] = text

    memory_store_arn = shared._str_or_none(memory_info.get("memoryStoreArn"))
    if memory_store_arn is not None:
        attributes["memoryStoreArn"] = memory_store_arn

    update_expression, expr_names, expr_values = shared._build_update_expression(attributes)
    db = shared._db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    try:
        response = db.update_item(
            shared._tenants_table_name(),
            key=shared._tenant_key(tenant_id),
            update_expression=update_expression,
            expression_attribute_values=expr_values,
            expression_attribute_names=expr_names,
            condition_expression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except shared.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return shared._error(409, "CONFLICT", "Tenant already exists")
        raise

    item = response.get("Attributes", {})
    shared._put_event(
        deps,
        detail_type="tenant.created",
        detail={
            "tenantId": tenant_id,
            "appId": app_id,
            "tier": tier,
            "accountId": attributes["accountId"],
            "actorSub": caller.sub,
        },
    )
    return shared._response(201, {"tenant": shared._serialize_tenant(item)})


def system_caller_for_tenant(tenant_id: str, app_id: str | None) -> shared.CallerIdentity:
    return shared.CallerIdentity(
        tenant_id=tenant_id,
        app_id=app_id or "platform-provisioner",
        tier=shared.TenantTier.STANDARD.value,
        sub="platform-provisioner",
        roles=frozenset(shared._ADMIN_ROLES),
        usage_identifier_key=None,
    )


def handle_tenant_provisioning_event(
    event: dict[str, Any],
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    detail_type = shared._str_or_none(event.get("detail-type")) or ""
    detail = event.get("detail") or {}
    if not isinstance(detail, dict):
        raise ValueError("Event detail must be an object")

    tenant_id = shared._canonical_tenant_id(detail.get("tenantId"), allow_reserved=True)
    app_id = shared._str_or_none(detail.get("appId"))
    caller = system_caller_for_tenant(tenant_id, app_id)
    existing = shared._read_tenant_record(tenant_id=tenant_id, caller=caller, app_id=app_id)
    if existing is None:
        shared.logger.warning(
            "Provisioning event for unknown tenant",
            extra={"tenant_id": tenant_id},
        )
        return {
            "statusCode": 200,
            "body": shared.json.dumps({"status": "IGNORED", "tenantId": tenant_id}),
        }

    now = shared._iso(shared._now_utc())
    attrs: dict[str, Any] = {
        "updatedAt": now,
        "provisioningUpdatedAt": now,
    }
    if detail_type == "tenant.provisioned":
        attrs["provisioningStatus"] = "ready"
        field_aliases = {
            "executionRoleArn": ("executionRoleArn", "ExecutionRoleArn"),
            "memoryStoreArn": ("memoryStoreArn", "MemoryStoreArn"),
            "runtimeRegion": ("runtimeRegion", "RuntimeRegion"),
            "fallbackRegion": ("fallbackRegion", "FallbackRegion"),
        }
        for target_field, aliases in field_aliases.items():
            text = None
            for alias in aliases:
                text = shared._str_or_none(detail.get(alias))
                if text is not None:
                    break
            if text is not None:
                attrs[target_field] = text
        attrs["provisioningError"] = None
    elif detail_type == "tenant.provisioning_failed":
        attrs["provisioningStatus"] = "failed"
        attrs["provisioningError"] = shared._str_or_none(
            detail.get("reason") or detail.get("stackStatus") or detail.get("error")
        )
    else:
        return shared._response(200, {"status": "IGNORED", "detailType": detail_type})

    update_expression, expr_names, expr_values = shared._build_update_expression(attrs)
    db = shared._db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    response = db.update_item(
        shared._tenants_table_name(),
        key=shared._tenant_key(tenant_id),
        update_expression=update_expression,
        expression_attribute_values=expr_values,
        expression_attribute_names=expr_names,
        condition_expression="attribute_exists(PK) AND attribute_exists(SK)",
    )
    item = response.get("Attributes", {})
    return shared._response(
        200,
        {"tenant": shared._serialize_tenant(item), "eventType": detail_type},
    )


def usage_summary(
    deps: shared.TenantApiDependencies,
    *,
    tenant_id: str,
    app_id: str | None,
) -> dict[str, Any]:
    usage = deps.usage_client.get_tenant_usage(tenant_id=tenant_id, app_id=app_id)
    if not isinstance(usage, dict):
        return {}
    return usage


def handle_list(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
) -> dict[str, Any]:
    if not caller.is_admin:
        if caller.tenant_id:
            response = handle_read(event, caller, deps, tenant_id=caller.tenant_id)
            if response["statusCode"] == 200:
                body = shared.json.loads(response["body"])
                return shared._response(200, {"items": [body["tenant"]], "nextToken": None})
        return shared._response(200, {"items": [], "nextToken": None})

    query_params = event.get("queryStringParameters") or {}
    status_filter = shared._str_or_none(query_params.get("status"))
    tier_filter = shared._str_or_none(query_params.get("tier"))
    limit = min(int(query_params.get("limit", 50)), 100)
    next_token = query_params.get("nextToken")

    db = shared._control_plane_db(caller)

    scan_params: dict[str, Any] = {"limit": limit}
    if next_token:
        try:
            scan_params["exclusive_start_key"] = shared.json.loads(next_token)
        except shared.json.JSONDecodeError as exc:
            raise ValueError("Invalid nextToken") from exc

    filter_exprs = []
    expr_values = {}
    expr_names = {}
    if status_filter:
        filter_exprs.append("#s = :s")
        expr_names["#s"] = "status"
        expr_values[":s"] = status_filter.lower()
    if tier_filter:
        filter_exprs.append("#t = :t")
        expr_names["#t"] = "tier"
        expr_values[":t"] = tier_filter.lower()

    if filter_exprs:
        scan_params["filter_expression"] = " AND ".join(filter_exprs)
        scan_params["expression_attribute_names"] = expr_names
        scan_params["expression_attribute_values"] = expr_values

    result = db.scan(shared._tenants_table_name(), **scan_params)
    return shared._response(
        200,
        {
            "items": [shared._serialize_tenant(item) for item in result.items],
            "nextToken": (
                shared.json.dumps(result.last_evaluated_key) if result.last_evaluated_key else None
            ),
        },
    )


def handle_read(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    _ = event
    if not shared._can_read_tenant(caller, tenant_id):
        raise PermissionError("Caller may only read own tenant unless Platform.Admin")
    item = shared._read_tenant_record(tenant_id=tenant_id, caller=caller)
    if item is None:
        return shared._error(404, "NOT_FOUND", "Tenant not found")
    tenant = shared._serialize_tenant(item)
    tenant["usage"] = usage_summary(
        deps,
        tenant_id=tenant_id,
        app_id=shared._str_or_none(item.get("appId")),
    )
    if caller.usage_identifier_key:
        tenant["usage"]["usageIdentifierKey"] = caller.usage_identifier_key
    return shared._response(200, {"tenant": tenant})


def handle_update(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    shared._require_admin(caller)
    existing = shared._read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return shared._error(404, "NOT_FOUND", "Tenant not found")

    body = shared._require_json_body(event)
    allowed = {
        "tier",
        "monthlyBudgetUsd",
        "status",
        "executionRoleArn",
        "memoryStoreArn",
        "runtimeRegion",
        "fallbackRegion",
    }
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ValueError(f"Unsupported update field(s): {', '.join(unknown)}")
    if not body:
        raise ValueError("At least one update field is required")

    attrs: dict[str, Any] = {"updatedAt": shared._iso(shared._now_utc())}
    if "tier" in body:
        attrs["tier"] = shared._normalize_tier(body["tier"])
    if "monthlyBudgetUsd" in body:
        attrs["monthlyBudgetUsd"] = shared._as_float(
            body["monthlyBudgetUsd"], field="monthlyBudgetUsd"
        )
    if "status" in body:
        attrs["status"] = shared._normalize_status(body["status"])
    for field in ("executionRoleArn", "memoryStoreArn", "runtimeRegion", "fallbackRegion"):
        if field in body:
            attrs[field] = shared._str_or_none(body[field])

    update_expression, expr_names, expr_values = shared._build_update_expression(attrs)
    db = shared._db_for_tenant(
        tenant_id=tenant_id,
        caller=caller,
        app_id=shared._str_or_none(existing.get("appId")),
    )
    response = db.update_item(
        shared._tenants_table_name(),
        key=shared._tenant_key(tenant_id),
        update_expression=update_expression,
        expression_attribute_values=expr_values,
        expression_attribute_names=expr_names,
        condition_expression="attribute_exists(PK) AND attribute_exists(SK)",
    )
    item = response.get("Attributes", {})

    old_tier = shared._str_or_none(existing.get("tier"))
    new_tier = shared._str_or_none(item.get("tier"))
    detail_type = "tenant.updated"
    detail: dict[str, Any] = {"tenantId": tenant_id, "actorSub": caller.sub}
    if old_tier != new_tier and new_tier is not None:
        detail_type = "tenant.tier_changed"
        detail["oldTier"] = old_tier
        detail["newTier"] = new_tier
    shared._put_event(deps, detail_type=detail_type, detail=detail)
    return shared._response(200, {"tenant": shared._serialize_tenant(item)})


def handle_delete(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    shared._require_admin(caller)
    existing = shared._read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return shared._error(404, "NOT_FOUND", "Tenant not found")

    now = shared._now_utc()
    purge_at = int((now + shared.timedelta(days=shared._DELETE_RETENTION_DAYS)).timestamp())
    attrs = {
        "status": shared.TenantStatus.DELETED.value,
        "updatedAt": shared._iso(now),
        "deletedAt": shared._iso(now),
        "purgeAtEpochSeconds": purge_at,
    }
    db = shared._db_for_tenant(
        tenant_id=tenant_id,
        caller=caller,
        app_id=shared._str_or_none(existing.get("appId")),
    )
    update_expression, expr_names, expr_values = shared._build_update_expression(attrs)
    response = db.update_item(
        shared._tenants_table_name(),
        key=shared._tenant_key(tenant_id),
        update_expression=update_expression,
        expression_attribute_values=expr_values,
        expression_attribute_names=expr_names,
        condition_expression="attribute_exists(PK) AND attribute_exists(SK)",
    )
    item = response.get("Attributes", {})
    shared._put_event(
        deps,
        detail_type="tenant.deleted",
        detail={
            "tenantId": tenant_id,
            "actorSub": caller.sub,
            "retentionDays": shared._DELETE_RETENTION_DAYS,
            "purgeAtEpochSeconds": purge_at,
        },
    )
    return shared._response(200, {"tenant": shared._serialize_tenant(item)})


def handle_rotate_api_key(
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not shared._can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    existing = shared._read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return shared._error(404, "NOT_FOUND", "Tenant not found")

    app_id = shared._str_or_none(existing.get("appId"))
    secret_arn = shared._str_or_none(existing.get("apiKeySecretArn"))
    if app_id is None or secret_arn is None:
        return shared._error(409, "CONFLICT", "Tenant is missing API key secret metadata")

    rotated_at = shared._now_utc()
    secret_value = {
        "tenantId": tenant_id,
        "appId": app_id,
        "apiKey": shared.secrets.token_urlsafe(32),
        "rotatedAt": shared._iso(rotated_at),
    }
    put_response = deps.secretsmanager.put_secret_value(
        SecretId=secret_arn,
        SecretString=shared.json.dumps(secret_value),
    )

    db = shared._db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    update_expression, expr_names, expr_values = shared._build_update_expression(
        {"updatedAt": shared._iso(rotated_at)}
    )
    db.update_item(
        shared._tenants_table_name(),
        key=shared._tenant_key(tenant_id),
        update_expression=update_expression,
        expression_attribute_values=expr_values,
        expression_attribute_names=expr_names,
        condition_expression="attribute_exists(PK) AND attribute_exists(SK)",
    )

    shared._put_event(
        deps,
        detail_type="tenant.api_key_rotated",
        detail={
            "tenantId": tenant_id,
            "appId": app_id,
            "actorSub": caller.sub,
            "secretArn": secret_arn,
        },
    )
    return shared._response(
        200,
        {
            "tenantId": tenant_id,
            "apiKeySecretArn": secret_arn,
            "rotatedAt": shared._iso(rotated_at),
            "versionId": shared._str_or_none(put_response.get("VersionId")),
        },
    )


def handle_invite_user(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    if not shared._can_manage_tenant_self_service(caller, tenant_id):
        raise PermissionError("Caller requires a tenant self-service admin role")

    existing = shared._read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return shared._error(404, "NOT_FOUND", "Tenant not found")

    body = shared._require_json_body(event)
    email = shared._str_or_none(body.get("email"))
    if email is None or "@" not in email:
        raise ValueError("email is required and must be a valid email address")

    role = shared._normalize_tenant_invite_role(body.get("role"))
    display_name = shared._str_or_none(body.get("displayName"))
    app_id = shared._str_or_none(existing.get("appId"))

    invite_id = f"invite-{shared.secrets.token_hex(8)}"
    now = shared._now_utc()
    expires_at = now + shared.timedelta(days=shared._INVITE_EXPIRY_DAYS)
    invite = {
        "PK": f"TENANT#{tenant_id}",
        "SK": f"INVITE#{invite_id}",
        "inviteId": invite_id,
        "tenantId": tenant_id,
        "email": email.lower(),
        "role": role,
        "displayName": display_name,
        "status": "pending",
        "createdAt": shared._iso(now),
        "expiresAt": shared._iso(expires_at),
    }

    db = shared._db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
    db.put_item(shared._tenants_table_name(), invite)
    shared._put_event(
        deps,
        detail_type="tenant.user_invited",
        detail={**invite, "actorSub": caller.sub, "appId": app_id},
    )
    return shared._response(
        202,
        {"invite": {k: v for k, v in invite.items() if k not in ("PK", "SK")}},
    )


def handle_list_invites(
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
        sk_condition=shared.Key("SK").begins_with("INVITE#"),
    )
    items = [{k: v for k, v in item.items() if k not in ("PK", "SK")} for item in result.items]
    return shared._response(200, {"items": items})


def handle_audit_export(
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    shared._require_admin(caller)
    existing = shared._read_tenant_record(tenant_id=tenant_id, caller=caller)
    if existing is None:
        return shared._error(404, "NOT_FOUND", "Tenant not found")

    query = shared._query_params(event)
    start_at = shared._parse_optional_utc_timestamp(query.get("start"), field="start")
    end_at = shared._parse_optional_utc_timestamp(query.get("end"), field="end")
    if start_at is not None and end_at is not None and start_at > end_at:
        raise ValueError("start must be less than or equal to end")

    bucket = shared._audit_export_bucket()
    if bucket is None:
        shared.logger.error("Audit export bucket is not configured")
        return shared._error(500, "INTERNAL_ERROR", "Audit export bucket is not configured")

    app_id = shared._str_or_none(existing.get("appId"))
    shared.logger.info(
        "Generating tenant audit export",
        extra={
            "target_tenantid": tenant_id,
            "window_start": shared._iso(start_at) if start_at is not None else None,
            "window_end": shared._iso(end_at) if end_at is not None else None,
        },
    )
    records = shared._collect_audit_export_records(
        tenant_id=tenant_id,
        caller=caller,
        app_id=app_id,
        start_at=start_at,
        end_at=end_at,
    )
    generated_at = shared._now_utc()
    object_key = shared._audit_export_key(tenant_id, generated_at)
    payload = shared._build_audit_export_payload(
        tenant_id=tenant_id,
        generated_at=generated_at,
        start_at=start_at,
        end_at=end_at,
        records=records,
    )

    try:
        tenant_s3 = shared._tenant_s3_for_scope(tenant_id=tenant_id, caller=caller, app_id=app_id)
        tenant_s3.put_object(bucket, object_key, payload, ContentType="application/json")
        expires_in = shared._audit_export_url_expiry_seconds()
        download_url = tenant_s3.generate_presigned_url(bucket, object_key, expires_in=expires_in)
    except Exception:
        shared.logger.exception("Failed to generate tenant audit export")
        return shared._error(500, "INTERNAL_ERROR", "Failed to generate audit export")

    shared.logger.info(
        "Generated tenant audit export",
        extra={
            "target_tenantid": tenant_id,
            "record_count": len(records),
            "object_key": object_key,
        },
    )
    expires_at = generated_at + shared.timedelta(seconds=shared._audit_export_url_expiry_seconds())
    return shared._response(
        200,
        {
            "tenantId": tenant_id,
            "downloadUrl": download_url,
            "expiresAt": shared._iso(expires_at),
        },
    )


def handle_health(deps: shared.TenantApiDependencies) -> dict[str, Any]:
    try:
        region_param = deps.ssm.get_parameter(Name=shared._runtime_region_param_name())
        runtime_region = region_param["Parameter"]["Value"]
    except Exception:
        shared.logger.warning("Failed to fetch runtime region from SSM, using default")
        runtime_region = shared.os.environ.get("RUNTIME_REGION_DEFAULT", "eu-west-1")

    return shared._response(
        200,
        {
            "status": "ok",
            "version": shared.os.environ.get("SERVICE_VERSION", "0.1.0"),
            "runtimeRegion": runtime_region,
            "timestamp": shared._iso(shared._now_utc()),
            "checks": {"tenantApi": {"status": "ok"}},
        },
    )


def handle_sessions(event: dict[str, Any], caller: shared.CallerIdentity) -> dict[str, Any]:
    if caller.tenant_id is None:
        return shared._error(400, "BAD_REQUEST", "tenant context missing")

    query = event.get("queryStringParameters") or {}
    limit_raw = query.get("limit", 50)
    try:
        limit = max(1, min(int(limit_raw), 100))
    except (TypeError, ValueError):
        return shared._error(400, "BAD_REQUEST", "limit must be an integer between 1 and 100")

    _ = limit
    return shared._error(
        501,
        "NOT_IMPLEMENTED",
        "Session listing is not available until tenant-backed session tracking is implemented",
    )


def dispatch_routes(
    path: str,
    method: str,
    event: dict[str, Any],
    caller: shared.CallerIdentity,
    deps: shared.TenantApiDependencies,
    tenant_id: str | None,
) -> dict[str, Any] | None:
    path_lower = path.lower()
    if path_lower == "/v1/tenants":
        if method == "POST":
            return handle_create(event, caller, deps)
        if method == "GET":
            return handle_list(event, caller, deps)

    if tenant_id is not None:
        tenant_base = f"/v1/tenants/{tenant_id}"
        if path_lower == f"{tenant_base}/api-key/rotate" and method == "POST":
            return handle_rotate_api_key(caller, deps, tenant_id=tenant_id)
        if path_lower == f"{tenant_base}/users/invites" and method == "GET":
            return handle_list_invites(caller, deps, tenant_id=tenant_id)
        if path_lower == f"{tenant_base}/users/invite" and method == "POST":
            return handle_invite_user(event, caller, deps, tenant_id=tenant_id)
        if path_lower == f"{tenant_base}/audit-export" and method == "GET":
            return handle_audit_export(event, caller, deps, tenant_id=tenant_id)
        if path_lower == tenant_base:
            if method == "GET":
                return handle_read(event, caller, deps, tenant_id=tenant_id)
            if method in {"PATCH", "PUT"}:
                return handle_update(event, caller, deps, tenant_id=tenant_id)
            if method == "DELETE":
                return handle_delete(caller, deps, tenant_id=tenant_id)

    return None
