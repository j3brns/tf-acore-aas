from __future__ import annotations

import json

from src.webhook_delivery import events, retry_policy


def test_parse_retry_record_returns_none_for_missing_fields():
    assert events.parse_retry_record({"body": json.dumps({"tenantId": "t-001"})}) is None


def test_parse_retry_record_parses_expected_payload():
    message = events.parse_retry_record(
        {
            "body": json.dumps(
                {
                    "tenantId": "t-001",
                    "appId": "app-001",
                    "jobId": "job-123",
                    "attempt": 3,
                }
            )
        }
    )

    assert message is not None
    assert message.tenant_id == "t-001"
    assert message.app_id == "app-001"
    assert message.job_id == "job-123"
    assert message.attempt == 3


def test_retry_policy_caps_jittered_delay():
    delay = retry_policy.retry_delay_seconds(attempt=12)
    assert 1 <= delay <= 900
