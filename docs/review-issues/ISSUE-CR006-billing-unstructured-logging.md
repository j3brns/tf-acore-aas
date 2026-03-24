# SPEC VIOLATION: Billing handler uses f-string logging — defeats structured JSON logging mandate

## Seq
855

## Depends on
none

## Problem

`src/billing/handler.py` uses Python f-string interpolation in logger calls:

```python
logger.info(f"Processing billing for {tenant_id} on {date_to_process.date()}")
logger.warning(f"Tenant {tenant_id} exceeded budget {budget} (cost={total_cost}). Suspending.")
logger.info(f"Found {len(tenants)} active/suspended tenants to process")
logger.info(f"Billing pipeline complete. Processed={processed}, Errors={errors}")
logger.warning(f"Failed to emit cost metrics for {tenant_id}: {exc}")
logger.exception(f"Failed to process tenant {tenant.get('tenant_id')}: {exc}")
```

CLAUDE.md absolute constraint #11:

> **appid and tenantid on every log line, metric dimension, and trace annotation.**

And CLAUDE.md technology stack mandates `aws_lambda_powertools Logger` with structured
JSON. F-string interpolation:
- Embeds values into the message string rather than top-level JSON fields.
- Prevents log aggregation / alerting on `tenantid` / `appid` as first-class fields.
- Makes log queries (e.g., `{ $.tenantid = "acme" }`) fail in CloudWatch Insights.
- Can leak sensitive data into unstructured message strings.

## Scope

Replace all f-string logger calls in `src/billing/handler.py` with keyword-argument
structured calls:

```python
# Before
logger.info(f"Processing billing for {tenant_id} on {date_to_process.date()}")

# After
logger.info("Processing billing for tenant", tenant_id=tenant_id, date=str(date_to_process.date()))
```

Also ensure `logger.append_keys(tenantid=tenant_id, appid=app_id)` is called at the
start of each tenant's processing (already partially done; verify all code paths).

## Test Plan

```bash
uv run pytest tests/unit/test_billing_handler.py -v
make validate-local
```

## Definition of Done

- No f-string interpolation in `logger.*` calls in `src/billing/handler.py`.
- Structured extra kwargs used for all variable fields.
- `make validate-local` passes.
- No unrelated files changed.

## Resolution

**Status: Fixed** — commit `c523d4a` (2026-03-22).

All f-string logger calls in `src/billing/handler.py` replaced with structured
keyword-argument calls (e.g. `logger.info("msg", tenant_id=tenant_id, ...)`).
`logger.append_keys(tenantid=..., appid=...)` verified present on all tenant
processing code paths. Values no longer embedded in message strings; CloudWatch
Insights queries on `tenantid` / `appid` fields now function correctly.
All 492 unit tests pass; `make validate-local` clean.
