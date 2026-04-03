from __future__ import annotations

import json
from typing import Any

import boto3
import requests

_sqs_client = None
_http_session = None


def get_sqs(*, region: str):
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client("sqs", region_name=region)
    return _sqs_client


def get_http_session():
    global _http_session
    if _http_session is None:
        _http_session = requests.Session()
    return _http_session


def deliver_webhook(
    *,
    webhook_url: str,
    payload_bytes: bytes,
    headers: dict[str, str],
    timeout_seconds: int,
) -> None:
    response = get_http_session().post(
        webhook_url,
        data=payload_bytes,
        headers=headers,
        timeout=timeout_seconds,
    )
    response.raise_for_status()


def enqueue_retry(
    *,
    region: str,
    queue_url: str,
    tenant_id: str,
    app_id: str,
    job_id: str,
    attempt: int,
    delay_seconds: int,
) -> None:
    get_sqs(region=region).send_message(
        QueueUrl=queue_url,
        DelaySeconds=delay_seconds,
        MessageBody=json.dumps(
            {
                "tenantId": tenant_id,
                "appId": app_id,
                "jobId": job_id,
                "attempt": attempt,
            }
        ),
    )


def send_to_dlq(
    *,
    region: str,
    queue_url: str | None,
    job_item: dict[str, Any],
    attempt: int,
    reason: str,
) -> None:
    if queue_url is None:
        return

    get_sqs(region=region).send_message(
        QueueUrl=queue_url,
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
