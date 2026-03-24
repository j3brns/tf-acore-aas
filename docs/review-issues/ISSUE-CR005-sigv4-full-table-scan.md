# PERFORMANCE: SigV4 authoriser does a full DynamoDB table scan on every machine-auth request

## Seq
854

## Depends on
none

## Problem

`resolve_sigv4_tenant_binding()` in `src/authoriser/handler.py` resolves a caller ARN
to a trusted tenant by scanning the entire tenants table:

```python
# src/authoriser/handler.py:129-156
while True:
    response = table.scan(**scan_kwargs)
    for item in response.get("Items", []):
        role_arn = item.get("executionRoleArn") ...
        if role_arn not in candidate_role_arns:
            continue
        ...
```

At N tenants this is O(N) DynamoDB reads on every SigV4-authenticated request.
This has three consequences:
1. **Latency** — full scans on large tenant tables add hundreds of milliseconds to
   authoriser cold/warm path.
2. **Cost** — every machine API call consumes read capacity proportional to tenant count.
3. **Throttling risk** — rapid machine-caller traffic can exhaust table read capacity
   and degrade all tenants.

The fix is a Global Secondary Index (GSI) on `executionRoleArn` enabling O(1) lookup.
The CDK `TenantStack` already writes `executionRoleArn` into the tenant record — it
just needs a GSI projection.

## Scope

- Add a GSI `gsi-execution-role-arn` to the `platform-tenants` DynamoDB table CDK
  definition (key: `executionRoleArn`, projection: KEYS_ONLY or relevant attributes).
- Update `resolve_sigv4_tenant_binding()` to use `table.query()` on the GSI instead
  of `table.scan()`.
- Add an in-Lambda cache (LRU or TTL dict) for the ARN→tenant binding with a 60s TTL
  to further amortise repeated calls from the same warm Lambda.
- Update the CDK Jest tests for the tenants table.
- Update the authoriser unit tests.

**Stop-and-ask gate:** Any change to DynamoDB table schema (adding a GSI) requires
operator review per CLAUDE.md. File this issue and await sign-off before implementing
the CDK change.

## Test Plan

```bash
uv run pytest tests/unit/test_authoriser.py -v
cd infra/cdk && npx jest test/platform-stack.test.ts
make validate-local
```

## Definition of Done

- GSI exists in CDK definition and passes cfn-guard.
- `resolve_sigv4_tenant_binding` uses `query()` on the GSI.
- In-memory cache with TTL reduces repeated lookups.
- Unit tests mock the GSI query path.
- `make validate-local` passes.

## Resolution

**Status: Partially fixed** — commit `c523d4a` (2026-03-22).

**Immediate mitigation applied:** An in-memory TTL cache (default 60 s, configurable
via `SIGV4_BINDING_CACHE_TTL_SECONDS` env var) added to `resolve_sigv4_tenant_binding()`
in `src/authoriser/handler.py`. Repeated ARN lookups from warm Lambda invocations now
skip DynamoDB entirely within the TTL window.

**Full fix (GSI) is pending operator sign-off** per the stop-and-ask gate in the
Scope section above. The DynamoDB schema change (adding `gsi-execution-role-arn`)
has not yet been merged. A follow-up issue should be raised once sign-off is granted.

All 492 unit tests pass; `make validate-local` clean.
