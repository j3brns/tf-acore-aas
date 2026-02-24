"""
bff.handler — Thin Backend-for-Frontend Lambda.

Handles two concerns only:
  - POST /v1/bff/token-refresh: Entra on-behalf-of token exchange
  - POST /v1/bff/session-keepalive: ping AgentCore Runtime to prevent idle timeout

Does NOT handle agent invocations.

Implemented in TASK-038.
ADRs: ADR-011
"""
