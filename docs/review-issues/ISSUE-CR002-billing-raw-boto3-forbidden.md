# BUG: Billing handler uses raw boto3 DynamoDB for tenant suspension — violates CLAUDE.md absolute constraint

## Seq
851

## Depends on
none

## Problem

The billing handler suspends tenants by calling the raw boto3 DynamoDB table directly:

```python
# src/billing/handler.py:239-249
table = _dynamodb.Table(TENANTS_TABLE)
table.update_item(
    Key={"PK": f"TENANT#{tenant_id}", "SK": "METADATA"},
    ...
)
```

CLAUDE.md absolute constraint #12:

> **data-access-lib is the only permitted way to access DynamoDB from Lambda handlers.**

This bypasses:
- Tenant isolation enforcement (`_validate_pk`)
- Audit-log structured error paths
- CloudWatch TenantAccessViolation metric emission
- Future policy enforcement in `TenantScopedDynamoDB`

## Scope

- Replace the raw `_dynamodb.Table(TENANTS_TABLE).update_item(...)` call with
  `TenantScopedDynamoDB.update_item()`.
- The tenant `TenantScopedDynamoDB` instance `db` is already constructed earlier
  in `_process_tenant()`; reuse it.
- Confirm the unit tests mock `TenantScopedDynamoDB`, not `boto3.resource`.

## Test Plan

```bash
uv run pytest tests/unit/test_billing_handler.py -v
make validate-local
```

## Definition of Done

- No raw `boto3.resource(...).Table(...)` calls remain in `src/billing/handler.py`.
- The suspension update goes through `TenantScopedDynamoDB`.
- Tests pass.
- `make validate-local` passes.
