"""
data_access.exceptions — Tenant isolation violation exceptions.

Implemented in TASK-013.
"""


class TenantAccessViolation(Exception):
    """
    Raised when an operation attempts to access data outside the caller's tenant partition.

    Every TenantAccessViolation must:
      - Be logged with tenant_id and caller_tenant_id
      - Emit a CloudWatch metric: namespace=platform/security, name=TenantAccessViolation
      - Trigger the RUNBOOK-003 alarm

    Attributes:
        tenant_id:        Tenant whose data was protected (the access target).
        caller_tenant_id: Tenant that attempted the cross-tenant access (the perpetrator).
        attempted_key:    The DynamoDB key dict repr or S3 object key that was attempted.

    Implemented in TASK-013.
    """

    def __init__(self, *, tenant_id: str, caller_tenant_id: str, attempted_key: str) -> None:
        self.tenant_id = tenant_id
        self.caller_tenant_id = caller_tenant_id
        self.attempted_key = attempted_key
        super().__init__(
            f"Tenant {caller_tenant_id!r} attempted to access {attempted_key!r} "
            f"belonging to tenant {tenant_id!r}"
        )
