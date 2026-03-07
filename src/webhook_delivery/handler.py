"""
webhook_delivery.handler — Webhook delivery Lambda.

Triggered by DynamoDB Stream on platform-jobs when status=completed or failed.
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
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Tracer
from boto3.dynamodb.types import TypeDeserializer
from data_access import TenantContext, TenantScopedDynamoDB
from data_access.models import TenantTier

logger = Logger(service="webhook-delivery")
tracer = Tracer()
deserializer = TypeDeserializer()

_TENANTS_TABLE = os.environ.get("TENANTS_TABLE", "platform-tenants")
_JOBS_TABLE = os.environ.get("JOBS_TABLE", "platform-jobs")
_SIGNATURE_HEADER = "X-Platform-Signature"

# Global client cache
_secrets_client = None
_dynamodb_resource = None


def get_secrets_client():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager", region_name=os.environ["AWS_REGION"])
    return _secrets_client


def get_dynamodb_resource():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
    return _dynamodb_resource


def _get_api_key(tenant_id: str, secret_arn: str) -> str:
    """Fetch API key from Secrets Manager."""
    client = get_secrets_client()
    try:
        response = client.get_secret_value(SecretId=secret_arn)
        secret_dict = json.loads(response["SecretString"])
        return str(secret_dict["apiKey"])
    except Exception:
        logger.exception(
            "Failed to fetch API key secret",
            extra={"tenant_id": tenant_id, "secret_arn": secret_arn},
        )
        raise


def _sign_payload(payload_bytes: bytes, api_key: str) -> str:
    """Generate HMAC-SHA256 signature for the payload."""
    return hmac.new(api_key.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def _send_webhook_with_retry(url: str, payload: dict[str, Any], api_key: str) -> bool:
    """POST to webhook URL with retries and backoff."""
    payload_json = json.dumps(payload)
    payload_bytes = payload_json.encode("utf-8")
    signature = _sign_payload(payload_bytes, api_key)

    headers = {
        "Content-Type": "application/json",
        _SIGNATURE_HEADER: signature,
        "User-Agent": "Platform-Webhook-Delivery/1.0",
    }

    attempts = 3
    backoff = [2, 4, 8]

    for i in range(attempts):
        req = urllib.request.Request(url, data=payload_bytes, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if 200 <= response.getcode() < 300:
                    logger.info(
                        "Webhook delivered successfully",
                        extra={"url": url, "status_code": response.getcode()},
                    )
                    return True
                else:
                    logger.warning(
                        "Webhook delivery failed with status code",
                        extra={"url": url, "status_code": response.getcode()},
                    )
        except urllib.error.HTTPError as e:
            logger.warning(
                "Webhook delivery HTTP error",
                extra={"url": url, "status_code": e.code, "reason": e.reason},
            )
            if 400 <= e.code < 500 and e.code not in [408, 429]:
                # Don't retry client errors unless they are timeout or throttling
                return False
        except Exception as e:
            logger.warning("Webhook delivery exception", extra={"url": url, "error": str(e)})

        if i < attempts - 1:
            time.sleep(backoff[i])

    return False


@tracer.capture_method
def _process_record(record: dict[str, Any]) -> None:
    """Process a single DynamoDB stream record."""
    if record["eventName"] not in ["INSERT", "MODIFY"]:
        return

    new_image = {k: deserializer.deserialize(v) for k, v in record["dynamodb"]["NewImage"].items()}

    job_id = new_image.get("jobId")
    tenant_id = new_image.get("tenant_id") or new_image.get("tenantId")
    status = new_image.get("status")
    webhook_url = new_image.get("webhook_url") or new_image.get("webhookUrl")
    delivered = new_image.get("webhook_delivered") or new_image.get("webhookDelivered", False)

    if not webhook_url or delivered:
        return

    if status not in ["completed", "failed"]:
        return

    if not tenant_id:
        logger.error("tenant_id missing in job record", extra={"job_id": job_id})
        return

    logger.append_keys(job_id=job_id, tenantid=tenant_id)
    logger.info("Starting webhook delivery", extra={"status": status, "url": webhook_url})

    # Fetch tenant record to get secret ARN
    # Use TenantScopedDynamoDB to stay compliant with CLAUDE.md
    context = TenantContext(
        tenant_id=tenant_id,
        app_id="webhook-delivery",
        tier=TenantTier.STANDARD,
        sub="system",
    )
    db = TenantScopedDynamoDB(context, dynamodb_resource=get_dynamodb_resource())

    tenant_record = db.get_item(_TENANTS_TABLE, {"PK": f"TENANT#{tenant_id}", "SK": "METADATA"})
    if not tenant_record:
        logger.error("Tenant record not found", extra={"tenant_id": tenant_id})
        return

    secret_arn = tenant_record.get("apiKeySecretArn")
    if not secret_arn:
        logger.error("Tenant has no apiKeySecretArn", extra={"tenant_id": tenant_id})
        return

    try:
        api_key = _get_api_key(tenant_id, secret_arn)
    except Exception:
        return  # Logged in _get_api_key

    payload = {
        "jobId": job_id,
        "tenantId": tenant_id,
        "agentName": new_image.get("agentName"),
        "status": status,
        "createdAt": new_image.get("createdAt"),
        "completedAt": new_image.get("completedAt"),
        "resultS3Key": new_image.get("resultS3Key"),
        "errorMessage": new_image.get("errorMessage"),
    }

    success = _send_webhook_with_retry(webhook_url, payload, api_key)

    if success:
        # Update job record to mark as delivered
        try:
            db.update_item(
                _JOBS_TABLE,
                key={"PK": f"JOB#{job_id}", "SK": "METADATA"},
                update_expression="SET webhookDelivered = :val",
                expression_attribute_values={":val": True},
            )
            logger.info("Job record updated: webhookDelivered=True")
        except Exception:
            logger.exception("Failed to update job record after successful delivery")


@logger.inject_lambda_context(log_event=False)
@tracer.capture_lambda_handler
def lambda_handler(event: dict[str, Any], context: Any) -> None:
    """Lambda entry point for DynamoDB Stream events."""
    records = event.get("Records", [])
    for record in records:
        try:
            _process_record(record)
        except Exception:
            logger.exception("Failed to process record")
            # We don't raise here to allow other records in the batch to be processed.
            # Lambda will retry the whole batch if it fails.
            # If we want per-record retry, we should use a DLQ or handle it carefully.
