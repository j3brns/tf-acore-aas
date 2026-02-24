"""
gateway.interceptors.response_interceptor — AgentCore Gateway RESPONSE interceptor.

On every tool response:
  - tools/list: filters to tools where tierMinimum <= tenant tier
  - tools/call: scans response for PII patterns and redacts before returning
    PII patterns loaded from SSM /platform/gateway/pii-patterns/default
    Patterns: UK NI number, NHS number, sort code, account number, email

Implemented in TASK-037.
ADRs: ADR-004
"""
