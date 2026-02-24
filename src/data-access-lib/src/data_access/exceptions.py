"""
data_access.exceptions — Tenant isolation violation exceptions.

Implemented in TASK-013.
"""


class TenantAccessViolation(Exception):
    """
    Raised when an operation attempts to access data outside the caller's tenant partition.

    Every TenantAccessViolation must:
      - Be logged with tenantId and callerTenantId
      - Emit a CloudWatch metric: platform.security.tenant_access_violation
      - Trigger the RUNBOOK-003 alarm

    Implemented in TASK-013.
    """
