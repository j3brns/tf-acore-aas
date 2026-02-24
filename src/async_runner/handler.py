"""
async_runner.handler — Async job tracking Lambda.

Monitors AgentCore Runtime sessions running background tasks.
Polls /ping for HealthyBusy status and updates JOB records in DynamoDB.

NOTE: This is NOT an SQS consumer. Async work runs inside the Runtime session
via app.add_async_task / app.complete_async_task (see ADR-010).

Implemented in TASK-046.
ADRs: ADR-010
"""
