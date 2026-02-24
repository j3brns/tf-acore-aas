"""
data_access.client — TenantScopedDynamoDB and TenantScopedS3.

Enforces tenant partition on every DynamoDB and S3 operation.
Raises TenantAccessViolation on any cross-tenant access attempt.

100% test coverage required (security-critical path).
See TASK-013 for coverage assertions.

Implemented in TASK-013.
ADRs: ADR-012
"""


class TenantScopedDynamoDB:
    """
    DynamoDB client scoped to a single tenant partition.

    Enforces that all operations use keys prefixed with TENANT#{tenant_id}.
    Raises TenantAccessViolation if the caller attempts to access a different
    tenant's partition.

    Implemented in TASK-013.
    """


class TenantScopedS3:
    """
    S3 client scoped to a single tenant prefix.

    Enforces that all operations use keys under /tenants/{tenant_id}/.
    Raises TenantAccessViolation if the caller attempts to access a different
    tenant's prefix.

    Implemented in TASK-013.
    """
