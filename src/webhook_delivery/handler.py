"""
webhook_delivery.handler — Webhook delivery Lambda.

Triggered by EventBridge rule on DynamoDB Stream (platform-jobs) when status=complete.
POSTs to registered webhookUrl with HMAC-SHA256 signature.
Retries: 3 attempts, exponential backoff (2s, 4s, 8s).

Implemented in TASK-047.
ADRs: ADR-010
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext
from data_access import TenantScopedDynamoDB
from data_access.models import JobStatus, TenantContext, TenantTier, WebhookEventType, WebhookRecord

logger = Logger(service="webhook-delivery")
tracer = Tracer()

# Environment variables
JOBS_TABLE = os.environ.get("JOBS_TABLE", "platform-jobs")
WEBHOOKS_TABLE = os.environ.get("WEBHOOKS_TABLE", "platform-tenants")
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
BACKOFF_BASE = float(os.environ.get("BACKOFF_BASE", "2.0"))


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Process a job completion/failure event and deliver webhook."""
    del context

    # 1. Parse job record from event
    job_data = _extract_job_data(event)
    if not job_data:
        logger.info("Event does not contain a relevant job status change")
        return {"status": "skipped"}

    job_id = job_data["job_id"]
    tenant_id = job_data["tenant_id"]
    webhook_id = job_data.get("webhook_url")  # In our system, this field stores the ID
    status = job_data["status"]

    if not webhook_id:
        logger.info("Job has no webhook registered", extra={"job_id": job_id})
        return {"status": "skipped"}

    logger.append_keys(tenantid=tenant_id, jobid=job_id, webhookid=webhook_id)

    # 2. Resolve Webhook registration
    # Use a system context for lookup (webhook records are in the same partition)
    # Actually, WebhookRecord SK is TENANT#{tenant_id}, so it's scoped.
    tenant_context = TenantContext(
        tenant_id=tenant_id,
        app_id="webhook-delivery",
        tier=TenantTier.BASIC,  # Default tier for delivery service
        sub="webhook-delivery",
    )
    db = TenantScopedDynamoDB(tenant_context)

    webhook = _get_webhook(db, webhook_id, tenant_id)
    if not webhook:
        logger.warning("Webhook registration not found", extra={"webhook_id": webhook_id})
        return {"status": "webhook_not_found"}

    if not webhook.enabled:
        logger.info("Webhook is disabled", extra={"webhook_id": webhook_id})
        return {"status": "webhook_disabled"}

    # 3. Check if event type matches
    event_type = (
        WebhookEventType.JOB_COMPLETED
        if status == JobStatus.COMPLETED
        else WebhookEventType.JOB_FAILED
    )
    if event_type not in webhook.events:
        logger.info(
            "Webhook not registered for event type",
            extra={"event_type": event_type, "registered_events": webhook.events},
        )
        return {"status": "event_type_mismatch"}

    # 4. Deliver with retry logic
    payload = _build_payload(job_data, event_type)
    signature = _sign_payload(payload, webhook.secret)

    success = _deliver_with_retry(
        url=webhook.callback_url,
        payload=payload,
        signature=signature,
        signature_header=webhook.signature_header,
    )

    if success:
        _mark_delivered(db, job_id)
        logger.info("Webhook delivered successfully")
        return {"status": "delivered"}
    else:
        logger.error("Webhook delivery failed after exhaustion")
        # In a real system, we might want to alert ops here
        return {"status": "failed"}


def _extract_job_data(event: dict[str, Any]) -> dict[str, Any] | None:
    """Extract job data from EventBridge or DynamoDB Stream event."""
    # Check if it's an EventBridge event wrapping a DynamoDB record
    detail = event.get("detail")
    if isinstance(detail, dict):
        dynamodb = detail.get("dynamodb", {})
        new_image = dynamodb.get("NewImage")
        if new_image:
            return _parse_image(new_image)
        # If it's a direct detail payload (e.g. from a custom event)
        if "jobId" in detail and "status" in detail:
            return {
                "job_id": detail["jobId"],
                "tenant_id": detail["tenantId"],
                "status": JobStatus(detail["status"]),
                "webhook_url": detail.get("webhookUrl"),
                "agent_name": detail.get("agentName"),
                "result_s3_key": detail.get("resultS3Key"),
                "error_message": detail.get("errorMessage"),
            }

    # Check if it's a direct DynamoDB Stream event
    records = event.get("Records")
    if records and isinstance(records, list):
        # We only process the first record for simplicity in this Lambda
        # SQS trigger would have multiple records too
        record = records[0]
        dynamodb = record.get("dynamodb", {})
        new_image = dynamodb.get("NewImage")
        if new_image:
            return _parse_image(new_image)

    return None


def _parse_image(image: dict[str, Any]) -> dict[str, Any]:
    """Parse a DynamoDB JSON image into a flat dict."""
    # This is a simplified parser for common types
    result = {}
    for key, value in image.items():
        if "S" in value:
            result[key] = value["S"]
        elif "N" in value:
            result[key] = float(value["N"])
        elif "BOOL" in value:
            result[key] = value["BOOL"]

    # Map to internal field names if necessary
    return {
        "job_id": result.get("job_id"),
        "tenant_id": result.get("tenant_id"),
        "status": JobStatus(result.get("status")) if result.get("status") else None,
        "webhook_url": result.get("webhook_url"),
        "agent_name": result.get("agent_name"),
        "result_s3_key": result.get("result_s3_key"),
        "error_message": result.get("error_message"),
    }


def _get_webhook(db: TenantScopedDynamoDB, webhook_id: str, tenant_id: str) -> WebhookRecord | None:
    """Look up webhook registration."""
    key = {"PK": f"WEBHOOK#{webhook_id}", "SK": f"TENANT#{tenant_id}"}
    item = db.get_item(WEBHOOKS_TABLE, key)
    if not item:
        return None

    return WebhookRecord(
        webhook_id=str(item["webhook_id"]),
        tenant_id=str(item["tenant_id"]),
        callback_url=str(item["callback_url"]),
        events=[WebhookEventType(e) for e in item["events"]],
        secret=str(item["secret"]),
        created_at=str(item["created_at"]),
        description=item.get("description"),
        enabled=bool(item.get("enabled", True)),
        signature_header=item.get("signature_header", "X-Platform-Signature"),
    )


def _build_payload(job_data: dict[str, Any], event_type: WebhookEventType) -> dict[str, Any]:
    """Construct the webhook payload."""
    payload = {
        "eventType": str(event_type),
        "timestamp": datetime.now(UTC).isoformat(),
        "jobId": job_data["job_id"],
        "tenantId": job_data["tenant_id"],
        "agentName": job_data.get("agent_name"),
        "status": str(job_data["status"]),
    }
    if job_data.get("result_s3_key"):
        payload["resultS3Key"] = job_data["result_s3_key"]
    if job_data.get("error_message"):
        payload["errorMessage"] = job_data["error_message"]
    return payload


def _sign_payload(payload: dict[str, Any], secret: str) -> str:
    """Sign the payload using HMAC-SHA256."""
    message = json.dumps(payload, sort_keys=True).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return signature


def _deliver_with_retry(
    url: str,
    payload: dict[str, Any],
    signature: str,
    signature_header: str,
) -> bool:
    """Deliver webhook with manual retry logic."""
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        signature_header: signature,
        "User-Agent": "Platform-Webhook-Delivery/1.0",
    }

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            sleep_time = BACKOFF_BASE**attempt
            logger.info("Retrying delivery", extra={"attempt": attempt, "sleep_time": sleep_time})
            time.sleep(sleep_time)

        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    return True
                logger.warning("Webhook returned non-2xx", extra={"status": resp.status})
        except urllib.error.HTTPError as exc:
            logger.warning(
                "Webhook delivery HTTP error",
                extra={"status": exc.code, "attempt": attempt},
            )
            if 400 <= exc.code < 500:
                # Client error, don't retry unless it's 429
                if exc.code != 429:
                    return False
        except Exception as exc:
            logger.warning(
                "Webhook delivery exception",
                extra={"error": str(exc), "attempt": attempt},
            )

    return False


def _mark_delivered(db: TenantScopedDynamoDB, job_id: str) -> None:
    """Update job record to mark webhook as delivered."""
    key = {"PK": f"JOB#{job_id}", "SK": "METADATA"}
    db.update_item(
        JOBS_TABLE,
        key,
        update_expression="SET webhook_delivered = :true",
        expression_attribute_values={":true": True},
    )
