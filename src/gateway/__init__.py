"""
gateway — AgentCore Gateway interceptors package.

REQUEST interceptor: validates JWT, enforces tier, issues scoped act-on-behalf token.
RESPONSE interceptor: filters tools by tier, redacts PII.

Implemented in TASK-036 (request) and TASK-037 (response).
ADRs: ADR-004
"""
