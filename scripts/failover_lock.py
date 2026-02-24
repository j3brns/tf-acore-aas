"""
failover_lock.py — DynamoDB distributed lock for region failover.

Prevents race condition when multiple bridge Lambda instances attempt
simultaneous region failover.

Lock record: platform-ops-locks table, PK=LOCK#platform-runtime-failover
TTL: 5 minutes (auto-expire prevents permanent lock if operator disconnects)

Usage:
    uv run python scripts/failover_lock.py acquire --env <env>
    uv run python scripts/failover_lock.py release --env <env>

Acquire uses conditional write (fails if lock already held).
Release uses try/finally to ensure unlock even on error.

Implemented in TASK-030.
ADRs: ADR-009
"""
