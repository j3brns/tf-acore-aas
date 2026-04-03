from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests
from aws_lambda_powertools import Logger

from . import events, integrations, persistence, retry_policy


@dataclass(frozen=True)
class DeliveryConfig:
    region: str
    jobs_table: str
    tenants_table: str
    retry_queue_url: str | None
    dlq_url: str | None
    max_retry_attempts: int
    http_timeout_seconds: int
    delivered_status: str
    failed_status: str
    retrying_status: str
    skipped_status: str
    terminal_job_events: dict[str, str]


def process_stream_record(
    record: dict[str, Any], *, config: DeliveryConfig, logger: Logger
) -> None:
    dynamodb_record = record.get("dynamodb", {})
    new_image = events.deserialize_image(dynamodb_record.get("NewImage"))
    old_image = events.deserialize_image(dynamodb_record.get("OldImage"))
    if not events.should_process_stream_transition(
        new_image,
        old_image,
        terminal_job_events=config.terminal_job_events,
    ):
        return
    attempt_delivery(new_image, attempt=1, config=config, logger=logger)


def process_retry_record(record: dict[str, Any], *, config: DeliveryConfig, logger: Logger) -> None:
    retry_message = events.parse_retry_record(record)
    if retry_message is None:
        logger.warning("Ignoring malformed retry message")
        return

    job_item = persistence.load_retry_job(
        jobs_table=config.jobs_table,
        tenant_id=retry_message.tenant_id,
        app_id=retry_message.app_id,
        job_id=retry_message.job_id,
    )
    if job_item is None:
        logger.warning("Ignoring retry for missing job", extra={"job_id": retry_message.job_id})
        return
    attempt_delivery(job_item, attempt=retry_message.attempt, config=config, logger=logger)


def attempt_delivery(
    job_item: dict[str, Any],
    *,
    attempt: int,
    config: DeliveryConfig,
    logger: Logger,
) -> None:
    tenant_context = persistence.tenant_context_from_job(job_item)
    job_id = str(job_item.get("job_id", ""))
    logger.append_keys(appid=tenant_context.app_id, tenantid=tenant_context.tenant_id, jobid=job_id)

    event_name = config.terminal_job_events.get(str(job_item.get("status", "")))
    webhook_url = events.coerce_optional_string(job_item.get("webhook_url"))
    if event_name is None or webhook_url is None or bool(job_item.get("webhook_delivered", False)):
        return

    webhook_id = events.coerce_optional_string(job_item.get("webhook_id"))
    if webhook_id is None:
        _mark_and_send_dlq(
            config=config,
            tenant_context=tenant_context,
            job_id=job_id,
            job_item=job_item,
            attempt=attempt,
            error="Job is missing webhook_id required for signed delivery",
            reason="missing-webhook-id",
            logger=logger,
        )
        return

    registration = persistence.get_webhook_registration(
        tenants_table=config.tenants_table,
        tenant_context=tenant_context,
        webhook_id=webhook_id,
    )
    if registration is None:
        _mark_and_send_dlq(
            config=config,
            tenant_context=tenant_context,
            job_id=job_id,
            job_item=job_item,
            attempt=attempt,
            error=f"Webhook registration '{webhook_id}' not found",
            reason="missing-webhook-registration",
            logger=logger,
        )
        return

    if str(registration.get("status", "active")) != "active":
        persistence.mark_delivery_state(
            jobs_table=config.jobs_table,
            tenant_context=tenant_context,
            job_id=job_id,
            delivered=False,
            status=config.skipped_status,
            attempts=attempt,
            error="Webhook registration disabled",
        )
        return

    subscribed_events = registration.get("events") or []
    if event_name not in subscribed_events:
        persistence.mark_delivery_state(
            jobs_table=config.jobs_table,
            tenant_context=tenant_context,
            job_id=job_id,
            delivered=False,
            status=config.skipped_status,
            attempts=attempt,
            error=f"Webhook is not subscribed to {event_name}",
        )
        return

    signature_secret = events.coerce_optional_string(registration.get("signature_secret"))
    if signature_secret is None:
        _mark_and_send_dlq(
            config=config,
            tenant_context=tenant_context,
            job_id=job_id,
            job_item=job_item,
            attempt=attempt,
            error="Webhook registration missing signature secret",
            reason="missing-signature-secret",
            logger=logger,
        )
        return

    payload_bytes, headers = events.build_signed_request(job_item, event_name, signature_secret)

    try:
        integrations.deliver_webhook(
            webhook_url=webhook_url,
            payload_bytes=payload_bytes,
            headers=headers,
            timeout_seconds=config.http_timeout_seconds,
        )
    except requests.RequestException as exc:
        handle_delivery_failure(
            tenant_context=tenant_context,
            job_item=job_item,
            attempt=attempt,
            error=str(exc),
            config=config,
            logger=logger,
        )
        return

    persistence.mark_delivery_state(
        jobs_table=config.jobs_table,
        tenant_context=tenant_context,
        job_id=job_id,
        delivered=True,
        status=config.delivered_status,
        attempts=attempt,
        error=None,
    )
    logger.info("Webhook delivered")


def handle_delivery_failure(
    *,
    tenant_context,
    job_item: dict[str, Any],
    attempt: int,
    error: str,
    config: DeliveryConfig,
    logger: Logger,
) -> None:
    job_id = str(job_item.get("job_id", ""))
    if retry_policy.should_retry(attempt=attempt, max_retry_attempts=config.max_retry_attempts):
        persistence.mark_delivery_state(
            jobs_table=config.jobs_table,
            tenant_context=tenant_context,
            job_id=job_id,
            delivered=False,
            status=config.retrying_status,
            attempts=attempt,
            error=error,
        )
        if config.retry_queue_url is None:
            raise RuntimeError("WEBHOOK_RETRY_QUEUE_URL is not configured")
        integrations.enqueue_retry(
            region=config.region,
            queue_url=config.retry_queue_url,
            tenant_id=str(job_item.get("tenant_id", "")),
            app_id=str(job_item.get("app_id", "")),
            job_id=job_id,
            attempt=attempt + 1,
            delay_seconds=retry_policy.retry_delay_seconds(attempt=attempt),
        )
        logger.warning(
            "Webhook delivery failed, retry queued",
            extra={"attempt": attempt, "error": error},
        )
        return

    persistence.mark_delivery_state(
        jobs_table=config.jobs_table,
        tenant_context=tenant_context,
        job_id=job_id,
        delivered=False,
        status=config.failed_status,
        attempts=attempt,
        error=error,
    )
    integrations.send_to_dlq(
        region=config.region,
        queue_url=config.dlq_url,
        job_item=job_item,
        attempt=attempt,
        reason=error,
    )
    logger.error("Webhook delivery exhausted retries", extra={"attempt": attempt, "error": error})


def _mark_and_send_dlq(
    *,
    config: DeliveryConfig,
    tenant_context,
    job_id: str,
    job_item: dict[str, Any],
    attempt: int,
    error: str,
    reason: str,
    logger: Logger,
) -> None:
    persistence.mark_delivery_state(
        jobs_table=config.jobs_table,
        tenant_context=tenant_context,
        job_id=job_id,
        delivered=False,
        status=config.failed_status,
        attempts=attempt,
        error=error,
    )
    integrations.send_to_dlq(
        region=config.region,
        queue_url=config.dlq_url,
        job_item=job_item,
        attempt=attempt,
        reason=reason,
    )
