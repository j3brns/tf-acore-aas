"""webhook_delivery.handler — Async webhook delivery Lambda."""

from __future__ import annotations

import os
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

from . import integrations, service

logger = Logger(service="webhook-delivery")
tracer = Tracer()

JOBS_TABLE = os.environ.get("JOBS_TABLE", "platform-jobs")
TENANTS_TABLE = os.environ.get("TENANTS_TABLE", "platform-tenants")
WEBHOOK_RETRY_QUEUE_URL = os.environ.get("WEBHOOK_RETRY_QUEUE_URL")
WEBHOOK_DLQ_URL = os.environ.get("WEBHOOK_DLQ_URL")
WEBHOOK_MAX_RETRY_ATTEMPTS = int(os.environ.get("WEBHOOK_MAX_RETRY_ATTEMPTS", "3"))
WEBHOOK_HTTP_TIMEOUT_SECONDS = int(os.environ.get("WEBHOOK_HTTP_TIMEOUT_SECONDS", "10"))

TERMINAL_JOB_EVENTS = {
    "completed": "job.completed",
    "failed": "job.failed",
}
WEBHOOK_DELIVERY_DELIVERED = "delivered"
WEBHOOK_DELIVERY_FAILED = "failed"
WEBHOOK_DELIVERY_RETRYING = "retrying"
WEBHOOK_DELIVERY_SKIPPED = "skipped"


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


def _delivery_config() -> service.DeliveryConfig:
    return service.DeliveryConfig(
        region=os.environ["AWS_REGION"],
        jobs_table=JOBS_TABLE,
        tenants_table=TENANTS_TABLE,
        retry_queue_url=WEBHOOK_RETRY_QUEUE_URL,
        dlq_url=WEBHOOK_DLQ_URL,
        max_retry_attempts=WEBHOOK_MAX_RETRY_ATTEMPTS,
        http_timeout_seconds=WEBHOOK_HTTP_TIMEOUT_SECONDS,
        delivered_status=WEBHOOK_DELIVERY_DELIVERED,
        failed_status=WEBHOOK_DELIVERY_FAILED,
        retrying_status=WEBHOOK_DELIVERY_RETRYING,
        skipped_status=WEBHOOK_DELIVERY_SKIPPED,
        terminal_job_events=TERMINAL_JOB_EVENTS,
    )


def _handle_stream_record(record: dict[str, Any]) -> None:
    service.process_stream_record(record, config=_delivery_config(), logger=logger)


def _handle_retry_record(record: dict[str, Any]) -> None:
    service.process_retry_record(record, config=_delivery_config(), logger=logger)


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    return service.events.sign_payload(payload_bytes, secret)
