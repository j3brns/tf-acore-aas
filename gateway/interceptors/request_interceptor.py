"""
gateway.interceptors.request_interceptor — AgentCore Gateway REQUEST interceptor.

On every tool invocation:
  1. Validates Bearer JWT against Entra JWKS
  2. Checks tierMinimum for the requested tool — returns 403 if insufficient
  3. Issues scoped act-on-behalf token (5-minute TTL) for the specific tool
  4. Injects x-tenant-id, x-app-id, x-tier, x-acting-sub headers
  5. Enforces idempotency keyed on Mcp-Session-Id + body.id

The original user JWT never reaches a tool Lambda (see ADR-004).

Implemented in TASK-036.
ADRs: ADR-004
"""
