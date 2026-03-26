# ADR-016: Reserved Platform Internal Tenant for Operator-Controlled Agents

## Status: Accepted
## Date: 2026-03-26

## Context
The platform may need one or more internal agents to assist with control-plane work:
release governance, runbook guidance, tenant lifecycle workflows, operational triage,
and other platform-owned tasks.

The current design is explicitly multi-tenant and tenant-scoped:
- every operation must be tenant-scoped
- `appid` and `tenantid` must appear on every log line, metric, and trace
- tenant isolation is a core security invariant
- no superuser IAM roles in normal operation
- `data-access-lib` remains the only permitted DynamoDB interface in Lambda handlers

A naive "admin agent" design would create an undocumented bypass path or hidden
super-tenant. That would weaken tenant isolation, RBAC, and auditability. If the
platform introduces internal agent capability, it must preserve those invariants.

## Decision
The platform will define one reserved internal tenant identifier: `platform`.

The `platform` tenant is used only for platform-owned control-plane agents and
operator-assisted automation. It is a real tenant context for logs, metrics, traces,
and audit events. It is not a null/system mode and it is not a super-tenant.

Platform-owned agents do not receive implicit cross-tenant data access. Any action
against a customer tenant must flow through explicit admin/control-plane APIs or
workflows that validate the target tenant, enforce platform RBAC, and emit auditable
events.

The phrase "tenant 0" may be used conversationally, but it is not the canonical
identifier and must not appear in persisted IDs, APIs, or IAM/resource naming.

## Detailed Rules

### 1. Reserved Tenant Semantics
- `platform` is reserved for internal platform control-plane use.
- It must not be assignable to customer tenants.
- Tenant self-service creation flows must reject it.
- It must be handled as a first-class tenant context, not as a bypass case.
- It must exist in the `platform-tenants` table with valid metadata (status, tier, execution role).

### 2. Authorization Model
- Human operators authenticate via Entra as normal.
- Platform agents may be invoked only by identities with approved platform roles such
  as `Platform.Admin` or `Platform.Operator`, depending on route and action.
- Platform-agent authorization is additive to existing RBAC; it does not replace it.
- Machine-to-machine authentication for platform agents uses SigV4, consistent with other machine callers.

### 3. Cross-Tenant Actions
- Platform agents must not directly query or mutate arbitrary customer-tenant data
  stores merely because they run under the `platform` tenant.
- Cross-tenant operations must go through explicit control-plane APIs, service-layer
  operations, or orchestrations that:
  - validate the requested target tenant
  - enforce route/action-level RBAC
  - emit auditable events
  - preserve target-tenant identity in downstream calls

### 4. Data Access
- `data-access-lib` remains the only permitted DynamoDB interface in Lambda handlers.
- The `platform` tenant may access platform-owned control-plane records that are
  explicitly modeled as platform resources.
- Customer-tenant records remain customer-tenant scoped unless an approved
  control-plane path authorizes action against them.

### 5. Audit and Observability
Every platform-agent action must record:
- `tenantid=platform`
- acting user or service principal
- target tenant, where applicable
- target resource or workflow
- reason or operation type
- request and result status

This applies to logs, metrics, traces, and emitted audit events.

### 6. API and Control-Plane Boundary
Platform agents are control-plane actors. They are not a shortcut around the control
plane.

They may:
- assist operators with runbooks and diagnostics
- initiate approved admin workflows
- summarize platform state already available to the operator
- orchestrate release and onboarding operations through approved interfaces

They may not:
- bypass control-plane APIs
- assume broad direct access to all tenant data
- operate as an undocumented superuser path

## Performance and SigV4 (CR005)
To ensure the `platform` tenant does not degrade authoriser performance:
- The `platform` tenant record must include a valid `executionRoleArn`.
- The authoriser's SigV4 resolution path must treat `platform` like any other tenant.
- The GSI-based lookup (long-term fix for CR005) will ensure O(1) resolution for the
  `platform` tenant role ARN, avoiding table scans.

## Consequences

### Positive
- Preserves tenant-scoped architecture while allowing internal control-plane agents
- Improves auditability compared with ad hoc admin scripts or hidden bypass paths
- Creates a clean foundation for operator-assistance agents, release agents, and other
  platform-owned automation
- Keeps platform-owned automation visible in logs and traces as a distinct tenant
  context

### Negative
- Adds complexity to tenant modeling and reserved-tenant validation
- Requires explicit handling in auth, API, provisioning, and audit layers
- May require additional platform-owned resource modeling distinct from customer data

### Operational Impact
- `tenant-api` validation must reject `platform` for new tenant creation.
- `scripts/bootstrap.py` and `scripts/dev-bootstrap.py` must seed the `platform` tenant.
- architecture docs must distinguish customer tenants from the platform tenant
- threat model must account for misuse of platform-agent authority
- API and workflow docs must describe how target-tenant actions are authorized and
  audited

## Alternatives Rejected
- **No platform tenant; use direct admin scripts only**: keeps operator automation
  fragmented and less auditable.
- **Super-tenant with implicit cross-tenant access**: violates tenant isolation and
  creates an unsafe hidden privilege boundary.
- **Null tenant / system mode with no tenant context**: breaks the rule that every
  operation is tenant-scoped and degrades observability consistency.
- **Per-operation temporary bypass roles as the default model**: harder to reason
  about operationally and more likely to drift into unaudited privilege escalation.

## Implementation Details
1. Add `platform` to `_RESERVED_TENANT_IDS` in `src/tenant_api/handler.py`.
2. Seed the `platform` tenant in `scripts/dev-bootstrap.py` for local development.
3. Seed the `platform` tenant in `scripts/bootstrap.py` for environment bootstrap.
4. Ensure `authoriser/handler.py` correctly resolves the `platform` tenant via SigV4.
5. Add tests proving `platform` is reserved and correctly authenticated.
