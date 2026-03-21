# ADR-015: Agent Release Governance and Rollback Model

## Status: Proposed
## Date: 2026-03-21

## Context
Currently, agent registration and rollback are performed by scripts that directly mutate the `platform-agents` DynamoDB table. The current rollback mechanism deletes the "bad" version from the database. 

This model has several weaknesses:
1.  **Auditability:** Deleting records destroys the audit trail of what was actually running at any given time.
2.  **Governance:** Direct database access bypasses the Platform API's RBAC and audit logging.
3.  **Safety:** There is no formal "Pending" state for new versions; registration immediately makes a version "live" if it has the highest semver.
4.  **Compliance:** Production systems with compliance obligations must maintain immutable records of all software versions deployed.

## Decision
The platform will adopt a "Status-Based" release governance and rollback model.

### 1. Agent Status Lifecycle
Every `AgentRecord` in DynamoDB will include a `status` field:
-   `PENDING`: Version is registered and artifacts are uploaded, but it is not yet invokable by tenants.
-   `RELEASED`: Version is approved and invokable. The Bridge picks the highest semver version with this status.
-   `ROLLBACK`: Version has been deactivated due to a reported issue. It is preserved for audit but never invoked.
-   `DEPRECATED`: Version is old but still invokable if explicitly requested by version (future capability), but not the default.

### 2. Immutability
Agent versions are immutable. Once a version (e.g., `v1.2.3`) is registered, its associated S3 keys, hashes, and configuration cannot be modified. Only the `status` and governance metadata (`approved_by`, etc.) may change.

### 3. Promotion Workflow
-   **Registration:** New versions are registered via a Platform API endpoint. In `dev` environments, they may default to `RELEASED`. In `prod`, they must start as `PENDING`.
-   **Approval:** An authorized operator (`Platform.Admin`) promotes a `PENDING` version to `RELEASED` via a PATCH operation.
-   **Bridge Resolution:** The Bridge Lambda finds the "active" version by querying the `platform-agents` table for the highest semver where `status = RELEASED`.

### 4. Rollback Mechanism
Rollback is a "forward" metadata transition:
1.  The operator identifies the bad version.
2.  The operator updates the bad version's status to `ROLLBACK`.
3.  The Bridge immediately stops using that version and falls back to the next-highest `RELEASED` version.
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
-   **Safety:** `PENDING` state allows smoke testing before broad release (if the Bridge supports it).

### Negative
-   **Storage:** Slightly higher DynamoDB storage (negligible for agent metadata).
-   **Complexity:** Requires Bridge to filter by status.
-   **Migration:** Existing `scripts/` need to be updated to call APIs instead of DDB.

## Implementation Notes
1.  Update `data-access-lib` models.
2.  Update `tenant_api` with new routes.
3.  Update `bridge` to filter for `status=RELEASED`.
4.  Update `scripts/register_agent.py` and `scripts/rollback_agent.py`.
5.  Perform a one-time backfill of `status=RELEASED` for existing agent records.
