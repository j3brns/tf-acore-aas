"""
data_access — Tenant-scoped AWS data access library.

The ONLY permitted way to access DynamoDB and S3 from Lambda handlers.
See CLAUDE.md forbidden patterns for the enforcement rationale.

Implemented in TASK-013.
ADRs: ADR-012
"""

from data_access.client import TenantScopedDynamoDB, TenantScopedS3
from data_access.exceptions import TenantAccessViolation
from data_access.models import TenantContext

__all__ = ["TenantContext", "TenantAccessViolation", "TenantScopedDynamoDB", "TenantScopedS3"]
