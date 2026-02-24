# ADR-013: Entra Group Membership as JWT Roles Claim for Platform RBAC

## Status: Accepted
## Date: 2026-02-24

## Context
Admin routes (tenant management, platform operations) must be restricted to authorised
operators. Options: a separate permissions service, a custom authorisation database,
resource-level IAM policies, or Entra group-to-role claim mapping.

## Decision
Entra group memberships are injected as roles claims in the JWT via Entra app registration
manifest configuration. The authoriser Lambda checks roles claim on admin-scoped routes.

Groups to roles mapping:
- platform-admins → Platform.Admin (full admin access)
- platform-operators → Platform.Operator (operational access, no tenant delete)
- agent-developers → Agent.Developer (agent push pipeline)
- tenant-* → Agent.Invoke tier:{basic|standard|premium}

## Consequences
- No additional service or database for authorisation
- Authoriser Lambda extended by ~10 lines to check roles claim
- Entra group membership managed by identity team — single source of truth
- Role changes take effect at next JWT refresh (TTL 1 hour)
- Roles visible in JWT — not secret, but not sensitive

## Alternatives Rejected
- Separate permissions service: additional infrastructure, additional operational burden
- Custom authorisation database: another DynamoDB table to maintain, stale data risk
- IAM resource policies: cannot express application-level roles through IAM cleanly
