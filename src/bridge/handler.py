"""
bridge.handler — Agent invocation bridge Lambda.

Reads invocation_mode from agent registry, assumes tenant execution role,
and routes to AgentCore Runtime via sync, streaming, or async paths.

Implemented in TASK-018.
ADRs: ADR-005, ADR-009, ADR-010
"""
