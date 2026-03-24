# BUG: TenantScopedS3.list_objects silently truncates at 1000 objects

## Seq
853

## Depends on
none

## Problem

`TenantScopedS3.list_objects()` in `src/data-access-lib/src/data_access/client.py`
calls `list_objects_v2` without a pagination loop:

```python
def list_objects(self, bucket: str, prefix: str = "") -> list[dict[str, Any]]:
    full_prefix = self._prefix + prefix
    response = self._s3.list_objects_v2(Bucket=bucket, Prefix=full_prefix)
    return response.get("Contents", [])   # <-- returns at most 1000 items
```

`list_objects_v2` returns a maximum of 1000 keys per call. When a tenant has more
than 1000 stored objects (common for job results buckets at scale), the method
silently returns an incomplete list. Callers have no way to know the result is
truncated.

This is a data-integrity bug: any feature that relies on `list_objects` for
completeness (e.g., cleanup jobs, audit exports, quota enforcement) will silently
operate on partial data.

## Scope

- Add a pagination loop to `list_objects()` using `ContinuationToken`.
- Add a new `list_objects_all()` method that returns the complete paginated list
  (matching the pattern of `query_all` / `scan_all` in `TenantScopedDynamoDB`), OR
  rename the existing method to make its behaviour clear.
- Update `tests/unit/test_models.py` (or the appropriate data-access-lib test file)
  to assert correct pagination behaviour with >1000 mocked objects.

## Test Plan

```bash
uv run pytest tests/unit/ -k "test_s3" -v
make validate-local
```

## Definition of Done

- `list_objects` (or equivalent) paginates correctly and returns all objects.
- Unit test verifies multi-page scenario.
- `make validate-local` passes.
- No unrelated files changed.
