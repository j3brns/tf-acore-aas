# ADR-015: Agent Release Governance and Rollback Model

## Status: Proposed
## Date: 2026-03-21

## Context
Currently, agent registration and rollback are performed by scripts that directly mutate the `platform-agents` DynamoDB table. The current rollback mechanism deletes the "bad" version from the database. 

This model has several weaknesses:
1.  **Auditability:** Deleting records destroys the audit trail of what was actually running at any given time.
2.  **Governance:** Direct database access bypasses the Platform API's RBAC and audit logging.
3.  **Safety:** There is no explicit gated lifecycle between artifact registration and tenant-visible promotion.
4.  **Compliance:** Production systems with compliance obligations must maintain immutable records of all software versions deployed.

## Decision
The platform will adopt a "Status-Based" release governance and rollback model.

### 1. Agent Release Status Lifecycle
`platform-agents` is the source of truth for the release state of an immutable built version.
Every `AgentRecord` in DynamoDB includes a `status` field with the canonical lifecycle:

-   `BUILT`: Artifacts are registered and immutable, but not yet deployed to staging.
-   `DEPLOYED_STAGING`: The built version is deployed to staging for verification.
-   `INTEGRATION_VERIFIED`: Staging integration checks passed.
-   `EVALUATION_PASSED`: Required evaluation gates passed.
-   `APPROVED`: An authorized operator recorded approval evidence for promotion.
-   `PROMOTED`: The version is tenant-invokable. The Bridge picks the highest semver version with this status.
-   `ROLLED_BACK`: A previously promoted version was withdrawn by operator action. It is preserved for audit and never invoked again.
-   `FAILED`: A pre-promotion gate failed. The version remains immutable and is not revived; a replacement must register as a new version.

Valid transitions are:

-   `BUILT -> DEPLOYED_STAGING | FAILED`
-   `DEPLOYED_STAGING -> INTEGRATION_VERIFIED | FAILED`
-   `INTEGRATION_VERIFIED -> EVALUATION_PASSED | FAILED`
-   `EVALUATION_PASSED -> APPROVED | FAILED`
-   `APPROVED -> PROMOTED | FAILED`
-   `PROMOTED -> ROLLED_BACK`

All other transitions are invalid.

### 2. Immutability
Agent versions are immutable. Once a version (e.g., `v1.2.3`) is registered, its associated S3 keys, hashes, and configuration cannot be modified. Only the `status` and governance metadata (`approved_by`, etc.) may change.

### 3. Promotion Workflow
-   **Registration:** New versions are registered via a Platform API endpoint in `BUILT` state after artifact upload/manifest validation.
-   **Verification Gates:** Platform operations advance the version through staging deploy, integration verification, evaluation, and approval states.
-   **Promotion:** An authorized operator (`Platform.Admin`) promotes an `APPROVED` version to `PROMOTED` via a PATCH operation. Promotion is a control-plane status change against the already-built version; it is never an implicit rebuild.
-   **Bridge Resolution:** The Bridge Lambda finds the active version by querying the `platform-agents` table for the highest semver where `status = PROMOTED`.

### 4. Rollback Mechanism
Rollback is a "forward" metadata transition:
1.  The operator identifies the bad version.
2.  The operator updates the bad version's status from `PROMOTED` to `ROLLED_BACK`.
3.  The Bridge immediately stops using that version and falls back to the next-highest `PROMOTED` version.
4.  No records are deleted.

### 5. Platform API Ownership
Direct DynamoDB mutations for agent lifecycle are deprecated. All registration, promotion, and rollback operations must flow through the `tenant-api` (Northbound API) platform routes, ensuring:
-   Entra-based RBAC enforcement.
-   Consistent audit logging in the `platform-invocations` or a dedicated audit bus.
-   Validation of agent manifests and artifact existence.

## Detailed Rules

### Data Model Updates
Update `AgentRecord` to include:
-   `status`: (enum)
-   `approved_by`: (string) identity of the promoter
-   `approved_at`: (iso8601)
-   `release_notes`: (string, optional)

### API Contract
New routes in `openapi.yaml`:
-   `POST /v1/platform/agents/register`: Register new version.
-   `PATCH /v1/platform/agents/{agentName}/versions/{version}`: Update status (Promote/Rollback).
-   `GET /v1/platform/agents`: List agents with full governance metadata.

## Consequences

### Positive
-   **Full Audit Trail:** Every version ever deployed remains in the database.
-   **Compliance:** Meets requirements for controlled releases and non-destructive rollbacks.
-   **RBAC:** Leverages existing platform roles for release control.
-   **Safety:** Explicit pre-promotion states allow staging, integration, evaluation, and approval evidence before tenant exposure.

### Negative
-   **Storage:** Slightly higher DynamoDB storage (negligible for agent metadata).
-   **Complexity:** Requires Bridge to filter by status.
-   **Migration:** Existing `scripts/` need to be updated to call APIs instead of DDB.

## Implementation Notes
1.  Update `data-access-lib` models.
2.  Update `tenant_api` with new routes and transition validation.
3.  Update `bridge` to filter for `status=PROMOTED`.
4.  Update `scripts/register_agent.py` and `scripts/rollback_agent.py`.
5.  Perform a one-time backfill of legacy `released/pending/rollback` statuses into the canonical model.
