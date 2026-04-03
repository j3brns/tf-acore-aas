from __future__ import annotations

import json
import os
import secrets
from datetime import timedelta
from typing import Any

from boto3.dynamodb.conditions import Key

try:
    from . import auth, constants, db_factory, db_utils, http_utils, models, utils, validation
except (ImportError, ValueError):  # pragma: no cover
    from src.tenant_api import (
        auth,
        constants,
        db_factory,
        db_utils,
        http_utils,
        models,
        utils,
        validation,
    )


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


def _audit_export_sk_condition(*, start_at: Any, end_at: Any) -> Any:
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
    db = db_factory.db_for_tenant(tenant_id=tenant_id, caller=caller, app_id=app_id)
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
    generated_at = utils.now_utc()
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
        download_url = tenant_s3.generate_presigned_url(bucket, object_key, expires_in=expires_in)
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
