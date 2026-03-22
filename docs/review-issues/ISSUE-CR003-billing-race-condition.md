# BUG: Billing monthly accumulation has no optimistic locking — concurrent runs produce incorrect totals

## Seq
852

## Depends on
ISSUE-CR001, ISSUE-CR002

## Problem

`_process_tenant()` in `src/billing/handler.py` follows a read-modify-write pattern
with no atomic guard:

```python
# Read current summary
current_summary = db.get_item(TENANTS_TABLE, summary_key)

# Compute new totals (non-atomically)
total_input = day_input + int(current_summary.get("total_input_tokens", 0))
total_cost  = day_cost  + float(current_summary.get("total_cost_usd", 0.0))

# Write back — no condition
db.put_item(TENANTS_TABLE, new_summary)
```

If the billing Lambda runs twice concurrently (e.g., a manual backfill overlaps the
nightly run), or if the Lambda is retried after a partial write, one write will
overwrite the other and totals will be silently under-counted.

The fix is to replace `put_item` with an atomic `ADD` update expression, which lets
DynamoDB perform the increment atomically without a read-before-write:

```python
db.update_item(
    TENANTS_TABLE,
    key=summary_key,
    update_expression=(
        "SET #ym = :ym, #tid = :tid, #lu = :lu "
        "ADD total_input_tokens :di, total_output_tokens :do, total_cost_usd :dc"
    ),
    expression_attribute_values={
        ":ym": year_month,
        ":tid": tenant_id,
        ":lu": _iso_now(),
        ":di": day_input,
        ":do": day_output,
        ":dc": Decimal(str(round(day_cost, 4))),
    },
    expression_attribute_names={
        "#ym": "year_month",
        "#tid": "tenant_id",
        "#lu": "last_updated",
    },
)
```

## Scope

- Remove the read + compute + `put_item` pattern in `_process_tenant()`.
- Replace with a single `update_item` ADD expression.
- Update unit tests to assert the atomic update path.

## Test Plan

```bash
uv run pytest tests/unit/test_billing_handler.py -v
make validate-local
```

## Definition of Done

- Billing accumulation uses DynamoDB atomic ADD expressions, no pre-read.
- Concurrent invocations produce correct totals.
- Tests pass and `make validate-local` passes.
