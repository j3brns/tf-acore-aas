"""
authoriser.handler — Lambda authoriser for Entra JWT and SigV4 paths.

Validates Bearer JWTs from Microsoft Entra ID and SigV4 signatures from
machine callers. Returns tenant context for downstream Lambdas.

Implemented in TASK-016.
ADRs: ADR-002, ADR-004
"""
