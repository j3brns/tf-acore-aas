# ADR-005: Declared Invocation Mode over Runtime Detection

## Status: Accepted
## Date: 2026-02-24

## Context
Previous design attempted to detect whether an agent invocation would exceed a timeout
by waiting for a threshold duration then switching to async. This is architecturally
incorrect: you cannot detect mid-execution what a call will do.

AgentCore Runtime natively supports:
- Synchronous invocation (max 15 minutes)
- Streaming invocation (max 15 minutes)
- Asynchronous via app.add_async_task / app.complete_async_task (max 8 hours)

## Decision
Agent declares its invocation mode in pyproject.toml [tool.agentcore.invocation_mode].
Values: sync | streaming | async. Bridge Lambda reads from agent registry (DynamoDB)
and routes accordingly. No runtime detection.

Async mode uses the AgentCore SDK native pattern:
- agent calls app.add_async_task() to register background work
- /ping returns HealthyBusy while background work runs
- agent calls app.complete_async_task() when done
- Session is NOT destroyed during HealthyBusy state

## Consequences
- Clear contract: clients know from agent metadata what mode to expect
- Async agents return 202 immediately — no heuristic timeout detection
- Sync agents always wait up to 15 minutes — no mid-stream mode switching
- Agent developer explicitly chooses the right pattern for their workload

## Alternatives Rejected
- Runtime detection at 25 seconds: wrong sync limit (15 minutes not 25 seconds),
  creates a race condition between multiple bridge Lambda instances
- Runtime detection at 15 minutes: impossible to know before reaching the limit
