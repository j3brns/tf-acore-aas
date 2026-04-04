from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from boto3.dynamodb.types import TypeDeserializer

from src.platform_utils import coerce_optional_string as _coerce_optional_string

WEBHOOK_SIGNATURE_HEADER = "X-Platform-Signature"
WEBHOOK_SIGNATURE_ALGORITHM = "HMAC-SHA256"

_deserializer = TypeDeserializer()


@dataclass(frozen=True)
class RetryMessage:
    tenant_id: str
    app_id: str
    job_id: str
    attempt: int


def parse_retry_record(record: dict[str, Any]) -> RetryMessage | None:
    body = json.loads(str(record.get("body", "{}")))
    tenant_id = coerce_optional_string(body.get("tenantId"))
    app_id = coerce_optional_string(body.get("appId"))
    job_id = coerce_optional_string(body.get("jobId"))
    attempt = int(body.get("attempt", 2))

    if tenant_id is None or app_id is None or job_id is None:
        return None
    return RetryMessage(tenant_id=tenant_id, app_id=app_id, job_id=job_id, attempt=attempt)


def deserialize_image(image: Any) -> dict[str, Any]:
    if not isinstance(image, dict):
        return {}
    return {key: _deserializer.deserialize(value) for key, value in image.items()}


def should_process_stream_transition(
    new_image: dict[str, Any],
    old_image: dict[str, Any] | None,
    *,
    terminal_job_events: dict[str, str],
) -> bool:
    if not new_image:
        return False

    status = coerce_optional_string(new_image.get("status"))
    if status not in terminal_job_events:
        return False

    if coerce_optional_string(new_image.get("webhook_url")) is None:
        return False

    if bool(new_image.get("webhook_delivered", False)):
        return False

    if old_image:
        old_status = coerce_optional_string(old_image.get("status"))
        if old_status in terminal_job_events:
            return False

    return True


def build_payload(job_item: dict[str, Any], event_name: str) -> dict[str, Any]:
    return {
        "event": event_name,
        "jobId": str(job_item.get("job_id", "")),
        "tenantId": str(job_item.get("tenant_id", "")),
        "agentName": str(job_item.get("agent_name", "")),
        "status": str(job_item.get("status", "")),
        "createdAt": coerce_optional_string(job_item.get("created_at")),
        "completedAt": coerce_optional_string(job_item.get("completed_at")),
        "errorMessage": coerce_optional_string(job_item.get("error_message")),
    }


def sign_payload(payload_bytes: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_signed_request(
    job_item: dict[str, Any],
    event_name: str,
    signature_secret: str,
) -> tuple[bytes, dict[str, str]]:
    payload = build_payload(job_item, event_name)
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return payload_bytes, {
        "Content-Type": "application/json",
        WEBHOOK_SIGNATURE_HEADER: sign_payload(payload_bytes, signature_secret),
    }


def coerce_optional_string(value: Any) -> str | None:
    return _coerce_optional_string(value)
