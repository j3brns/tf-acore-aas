# ADR-010: AgentCore Native Async Pattern over SQS Routing

## Status: Accepted
## Date: 2026-02-24

## Context
Previous design used an SQS queue to route long-running agent invocations to a separate
Lambda. This misunderstands how AgentCore handles async work.

AgentCore Runtime natively supports async via:
- app.add_async_task() — registers a background task, session stays HealthyBusy
- app.complete_async_task() — signals completion
- /ping returns HealthyBusy while work is in progress (prevents 15-min idle timeout)

## Decision
Async agents use the AgentCore SDK native pattern. The bridge Lambda submits the
invocation and tracks the job in DynamoDB. The agent code manages its own background
work lifecycle using the SDK. SQS is NOT used for agent invocation routing.

SQS IS used for webhook delivery retry (separate concern).

## Consequences
- Simpler architecture: no separate async-runner Lambda or SQS consumer
- Session remains alive during background work via HealthyBusy ping
- Agent developer writes app.add_async_task / app.complete_async_task directly
- Bridge Lambda polls session status via AgentCore API until completion
- 8-hour limit is enforced by Runtime, not by the platform queue

## Alternatives Rejected
- SQS-triggered Lambda for async invocation: misroutes invocation; SQS Lambda
  timeout is 15 minutes, not 8 hours; adds unnecessary hop
- EventBridge: 1MB payload limit, no visibility timeout for retry
