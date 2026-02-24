"""
webhook_delivery.handler — Webhook delivery Lambda.

Triggered by DynamoDB Stream on platform-jobs when status=complete.
POSTs to registered webhookUrl with HMAC-SHA256 signature.
Retries: 3 attempts, exponential backoff (2s, 4s, 8s).

Implemented in TASK-047.
ADRs: ADR-010
"""
