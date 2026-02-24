"""
tenant_api.handler — Tenant management REST API Lambda.

Handles CRUD for tenants: create, read, update, soft-delete.
Uses data-access-lib exclusively. Publishes EventBridge events on mutations.

Implemented in TASK-017.
ADRs: ADR-012
"""
