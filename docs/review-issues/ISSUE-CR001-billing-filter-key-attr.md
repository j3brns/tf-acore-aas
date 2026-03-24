# BUG: Billing pipeline crashes at runtime — Key() used in filter_expression instead of Attr()

## Seq
850

## Depends on
none

## Problem

`_get_active_tenants()` in `src/billing/handler.py` passes a `boto3.dynamodb.conditions.Key()`
expression as the `filter_expression` argument of `TenantScopedDynamoDB.scan_all()`:

```python
# src/billing/handler.py:108-112
return db.scan_all(
    TENANTS_TABLE,
    filter_expression=Key("SK").eq("METADATA")
    & (Key("status").eq(TenantStatus.ACTIVE) | Key("status").eq(TenantStatus.SUSPENDED)),
)
```

`Key()` is a `ConditionBase` subclass restricted to `KeyConditionExpression` arguments.
The `FilterExpression` parameter of `DynamoDB.scan` must use `Attr()` objects.
At runtime boto3 will raise a `ParamValidationError`, causing the entire billing
pipeline Lambda to fail for every scheduled invocation — no tenants are processed.

## Scope

- Replace all three `Key(...)` calls in `_get_active_tenants()` with `Attr(...)`.
- Update the import in `src/billing/handler.py`.
- Add / extend unit tests in `tests/unit/test_billing_handler.py` to call
  `_get_active_tenants()` through a mocked `TenantScopedDynamoDB` and verify the
  expression type.

## Test Plan

```bash
uv run pytest tests/unit/test_billing_handler.py -v
make validate-local
```

## Definition of Done

- `_get_active_tenants()` passes `Attr()` expressions to `scan_all()`.
- Unit tests verify `Attr` is used and not `Key`.
- `make validate-local` passes.
- No unrelated files changed.

## Resolution

**Status: Fixed** — commit `c523d4a` (2026-03-22).

All three `Key(...)` calls in `_get_active_tenants()` replaced with `Attr(...)`.
Import updated from `Key` to `Attr` in `src/billing/handler.py`.
Unit tests in `tests/unit/test_billing_pagination.py` updated to verify `Attr` usage.
All 492 unit tests pass; `make validate-local` clean.
