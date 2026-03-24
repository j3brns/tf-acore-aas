"""
webhook_delivery.handler — Async webhook delivery Lambda.

Consumes terminal job transitions from the platform-jobs DynamoDB stream and
uses an SQS retry queue for delivery retries. Delivery is signed with
HMAC-SHA256 using the webhook registration secret stored in platform-jobs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3
import requests
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.types import TypeDeserializer
from data_access import TenantContext, TenantScopedDynamoDB
from data_access.models import TenantTier

logger = Logger(service="webhook-delivery")
tracer = Tracer()

JOBS_TABLE = os.environ.get("JOBS_TABLE", "platform-jobs")
TENANTS_TABLE = os.environ.get("TENANTS_TABLE", "platform-tenants")
WEBHOOK_RETRY_QUEUE_URL = os.environ.get("WEBHOOK_RETRY_QUEUE_URL")
WEBHOOK_DLQ_URL = os.environ.get("WEBHOOK_DLQ_URL")
WEBHOOK_MAX_RETRY_ATTEMPTS = int(os.environ.get("WEBHOOK_MAX_RETRY_ATTEMPTS", "3"))
WEBHOOK_HTTP_TIMEOUT_SECONDS = int(os.environ.get("WEBHOOK_HTTP_TIMEOUT_SECONDS", "10"))
WEBHOOK_SIGNATURE_HEADER = "X-Platform-Signature"
WEBHOOK_SIGNATURE_ALGORITHM = "HMAC-SHA256"

TERMINAL_JOB_EVENTS = {
    "completed": "job.completed",
    "failed": "job.failed",
}
WEBHOOK_DELIVERY_DELIVERED = "delivered"
WEBHOOK_DELIVERY_FAILED = "failed"
WEBHOOK_DELIVERY_RETRYING = "retrying"
WEBHOOK_DELIVERY_SKIPPED = "skipped"

_deserializer = TypeDeserializer()
_sqs_client = None


def get_sqs():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client("sqs", region_name=os.environ["AWS_REGION"])
    return _sqs_client


@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    records = event.get("Records", [])
    if not records:
        logger.info("No webhook delivery records supplied")
        return {"status": "ignored"}

    batch_failures: list[dict[str, str]] = []
    for record in records:
        source = str(record.get("eventSource") or record.get("EventSource") or "")
        try:
            if source == "aws:dynamodb":
                _handle_stream_record(record)
            elif source == "aws:sqs":
                _handle_retry_record(record)
            else:
                logger.warning("Ignoring unsupported event source", extra={"event_source": source})
        except Exception:
            logger.exception("Webhook delivery processing failed", extra={"event_source": source})
            if source == "aws:sqs":
                message_id = str(record.get("messageId", ""))
                if message_id:
                    batch_failures.append({"itemIdentifier": message_id})
            else:
                raise

    if batch_failures:
        return {"batchItemFailures": batch_failures}
    return {"status": "ok"}


def _handle_stream_record(record: dict[str, Any]) -> None:
    dynamodb_record = record.get("dynamodb", {})
    new_image = _deserialize_image(dynamodb_record.get("NewImage"))
    old_image = _deserialize_image(dynamodb_record.get("OldImage"))
    if not _should_process_stream_transition(new_image, old_image):
        return
    _attempt_delivery(new_image, attempt=1)


def _handle_retry_record(record: dict[str, Any]) -> None:
    body = json.loads(str(record.get("body", "{}")))
    tenant_id = _coerce_optional_string(body.get("tenantId"))
    app_id = _coerce_optional_string(body.get("appId"))
    job_id = _coerce_optional_string(body.get("jobId"))
    attempt = int(body.get("attempt", 2))

    if tenant_id is None or app_id is None or job_id is None:
        logger.warning("Ignoring malformed retry message")
        return

    tenant_context = TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=TenantTier.BASIC,
        sub="webhook-delivery",
    )
    db = TenantScopedDynamoDB(tenant_context)
    job_item = db.get_item(JOBS_TABLE, {"PK": f"TENANT#{tenant_id}", "SK": f"JOB#{job_id}"})
    if job_item is None:
        logger.warning("Ignoring retry for missing job", extra={"job_id": job_id})
        return
    _attempt_delivery(job_item, attempt=attempt)


def _attempt_delivery(job_item: dict[str, Any], *, attempt: int) -> None:
    tenant_context = _tenant_context_from_job(job_item)
    job_id = str(job_item.get("job_id", ""))
    logger.append_keys(appid=tenant_context.app_id, tenantid=tenant_context.tenant_id, jobid=job_id)

    event_name = TERMINAL_JOB_EVENTS.get(str(job_item.get("status", "")))
    webhook_url = _coerce_optional_string(job_item.get("webhook_url"))
    if event_name is None or webhook_url is None or bool(job_item.get("webhook_delivered", False)):
        return

    webhook_id = _coerce_optional_string(job_item.get("webhook_id"))
    if webhook_id is None:
        _mark_delivery_state(
            tenant_context,
            job_id=job_id,
            delivered=False,
            status=WEBHOOK_DELIVERY_FAILED,
            attempts=attempt,
            error="Job is missing webhook_id required for signed delivery",
        )
        _send_to_dlq(job_item, attempt, "missing-webhook-id")
        return

    registration = _get_webhook_registration(tenant_context, webhook_id)
    if registration is None:
        _mark_delivery_state(
            tenant_context,
            job_id=job_id,
            delivered=False,
            status=WEBHOOK_DELIVERY_FAILED,
            attempts=attempt,
            error=f"Webhook registration '{webhook_id}' not found",
        )
        _send_to_dlq(job_item, attempt, "missing-webhook-registration")
        return

    if str(registration.get("status", "active")) != "active":
        _mark_delivery_state(
            tenant_context,
            job_id=job_id,
            delivered=False,
            status=WEBHOOK_DELIVERY_SKIPPED,
            attempts=attempt,
            error="Webhook registration disabled",
        )
        return

    subscribed_events = registration.get("events") or []
    if event_name not in subscribed_events:
        _mark_delivery_state(
            tenant_context,
            job_id=job_id,
            delivered=False,
            status=WEBHOOK_DELIVERY_SKIPPED,
            attempts=attempt,
            error=f"Webhook is not subscribed to {event_name}",
        )
        return

    signature_secret = _coerce_optional_string(registration.get("signature_secret"))
    if signature_secret is None:
        _mark_delivery_state(
            tenant_context,
            job_id=job_id,
            delivered=False,
            status=WEBHOOK_DELIVERY_FAILED,
            attempts=attempt,
            error="Webhook registration missing signature secret",
        )
        _send_to_dlq(job_item, attempt, "missing-signature-secret")
        return

    payload = _build_payload(job_item, event_name)
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        WEBHOOK_SIGNATURE_HEADER: _sign_payload(payload_bytes, signature_secret),
    }

    try:
        response = requests.post(
            webhook_url,
            data=payload_bytes,
            headers=headers,
            timeout=WEBHOOK_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        _handle_delivery_failure(
            tenant_context=tenant_context,
            job_item=job_item,
            attempt=attempt,
            error=str(exc),
        )
        return

    _mark_delivery_state(
        tenant_context,
        job_id=job_id,
        delivered=True,
        status=WEBHOOK_DELIVERY_DELIVERED,
        attempts=attempt,
        error=None,
    )
    logger.info("Webhook delivered")


def _handle_delivery_failure(
    *,
    tenant_context: TenantContext,
    job_item: dict[str, Any],
    attempt: int,
    error: str,
) -> None:
    job_id = str(job_item.get("job_id", ""))
    if attempt <= WEBHOOK_MAX_RETRY_ATTEMPTS:
        _mark_delivery_state(
            tenant_context,
            job_id=job_id,
            delivered=False,
            status=WEBHOOK_DELIVERY_RETRYING,
            attempts=attempt,
            error=error,
        )
        _enqueue_retry(job_item, attempt + 1, delay_seconds=min(900, 2**attempt))
        logger.warning(
            "Webhook delivery failed, retry queued",
            extra={"attempt": attempt, "error": error},
        )
        return

    _mark_delivery_state(
        tenant_context,
        job_id=job_id,
        delivered=False,
        status=WEBHOOK_DELIVERY_FAILED,
        attempts=attempt,
        error=error,
    )
    _send_to_dlq(job_item, attempt, error)
    logger.error("Webhook delivery exhausted retries", extra={"attempt": attempt, "error": error})


def _mark_delivery_state(
    tenant_context: TenantContext,
    *,
    job_id: str,
    delivered: bool,
    status: str,
    attempts: int,
    error: str | None,
) -> None:
    db = TenantScopedDynamoDB(tenant_context)
    db.update_item(
        JOBS_TABLE,
        {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": f"JOB#{job_id}"},
        (
            "SET webhook_delivered = :delivered, "
            "webhook_delivery_status = :status, "
            "webhook_delivery_attempts = :attempts, "
            "webhook_delivery_error = :error, "
            "webhook_last_attempt_at = :last_attempt_at"
        ),
        {
            ":delivered": delivered,
            ":status": status,
            ":attempts": attempts,
            ":error": error,
            ":last_attempt_at": datetime.now(UTC).isoformat(),
        },
    )


def _enqueue_retry(job_item: dict[str, Any], attempt: int, *, delay_seconds: int) -> None:
    if WEBHOOK_RETRY_QUEUE_URL is None:
        raise RuntimeError("WEBHOOK_RETRY_QUEUE_URL is not configured")

    get_sqs().send_message(
        QueueUrl=WEBHOOK_RETRY_QUEUE_URL,
        DelaySeconds=delay_seconds,
        MessageBody=json.dumps(
            {
                "tenantId": job_item.get("tenant_id"),
                "appId": job_item.get("app_id"),
                "jobId": job_item.get("job_id"),
                "attempt": attempt,
            }
        ),
    )


def _send_to_dlq(job_item: dict[str, Any], attempt: int, reason: str) -> None:
    if WEBHOOK_DLQ_URL is None:
        return

    get_sqs().send_message(
        QueueUrl=WEBHOOK_DLQ_URL,
        MessageBody=json.dumps(
            {
                "tenantId": job_item.get("tenant_id"),
                "appId": job_item.get("app_id"),
                "jobId": job_item.get("job_id"),
                "webhookId": job_item.get("webhook_id"),
                "attempt": attempt,
                "reason": reason,
            }
        ),
    )


def _get_webhook_registration(
    tenant_context: TenantContext, webhook_id: str
) -> dict[str, Any] | None:
    db = TenantScopedDynamoDB(tenant_context)
    record = db.get_item(
        TENANTS_TABLE,
        {"PK": f"TENANT#{tenant_context.tenant_id}", "SK": f"WEBHOOK#{webhook_id}"},
    )
    if record is None:
        return None
    if str(record.get("tenant_id", "")) != tenant_context.tenant_id:
        return None
    return record


def _tenant_context_from_job(job_item: dict[str, Any]) -> TenantContext:
    tenant_id = _coerce_optional_string(job_item.get("tenant_id"))
    app_id = _coerce_optional_string(job_item.get("app_id"))
    if tenant_id is None or app_id is None:
        raise ValueError("Job record is missing tenant_id or app_id")
    return TenantContext(
        tenant_id=tenant_id,
        app_id=app_id,
        tier=TenantTier.BASIC,
        sub="webhook-delivery",
    )


def _build_payload(job_item: dict[str, Any], event_name: str) -> dict[str, Any]:
    return {
        "event": event_name,
        "jobId": str(job_item.get("job_id", "")),
        "tenantId": str(job_item.get("tenant_id", "")),
        "agentName": str(job_item.get("agent_name", "")),
        "status": str(job_item.get("status", "")),
        "createdAt": _coerce_optional_string(job_item.get("created_at")),
        "completedAt": _coerce_optional_string(job_item.get("completed_at")),
        "errorMessage": _coerce_optional_string(job_item.get("error_message")),
    }


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _should_process_stream_transition(
    new_image: dict[str, Any], old_image: dict[str, Any] | None
) -> bool:
    if not new_image:
        return False

    status = _coerce_optional_string(new_image.get("status"))
    if status not in TERMINAL_JOB_EVENTS:
        return False

    if _coerce_optional_string(new_image.get("webhook_url")) is None:
        return False

    if bool(new_image.get("webhook_delivered", False)):
        return False

    if old_image:
        old_status = _coerce_optional_string(old_image.get("status"))
        if old_status in TERMINAL_JOB_EVENTS:
            return False

    return True


def _deserialize_image(image: Any) -> dict[str, Any]:
    if not isinstance(image, dict):
        return {}
    return {key: _deserializer.deserialize(value) for key, value in image.items()}


def _coerce_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)
