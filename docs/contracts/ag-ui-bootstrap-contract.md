# AG-UI Bootstrap Contract

Status: target-state contract

This document publishes the intended AG-UI bootstrap contract derived from
[ADR-018](../decisions/ADR-018-agentcore-ag-ui-integration.md).

This contract is the reviewable target for SPA-to-AgentCore AG-UI bootstrap.
Some fields are already present in the current implementation; some are
target-state and must not be assumed live until the implementing issues land.

## Scope

This contract applies only to human-facing SPA sessions that bootstrap an
AG-UI-capable agent through the platform control plane.

It does not replace:
- REST invoke flows for machine clients
- async job flows
- webhook delivery
- generic tenant API clients

## Endpoint

`POST /v1/agents/{agentName}/bootstrap`

Purpose:
- validate tenant identity and agent access
- confirm AG-UI support for the selected agent
- create a platform-tracked session record
- return constrained connection material for the SPA

## Request

Request body is optional.

Supported request fields:

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `sessionId` | string | no | Platform session identifier for reconnect flows when the SPA already has a platform session |
| `runtimeSessionId` | string | no | Runtime-facing session identifier for reconnect flows when the SPA already has a runtime session |

Notes:
- If omitted, the platform creates a new platform session and a new runtime session identifier.
- If reconnect semantics are not yet implemented in the current runtime build, callers must treat these fields as target-state only.

## Success Response

HTTP `200 OK`

Target-state response body:

```json
{
  "agentName": "echo-agent",
  "agentVersion": "1.3.0",
  "sessionId": "sess-123",
  "runtimeSessionId": "runtime-123",
  "startedAt": "2026-02-25T12:00:00Z",
  "expiresAt": "2026-02-25T12:15:00Z",
  "transport": "sse",
  "connectUrl": "https://ag-ui.example.com/connect",
  "tokenRefreshPath": "/v1/bff/token-refresh",
  "sessionKeepalivePath": "/v1/bff/session-keepalive",
  "auth": {
    "type": "oauth2_obo",
    "audience": "api://platform-dev",
    "scopeNames": ["Agent.AgUi.Connect"],
    "scopes": ["api://platform-dev/Agent.AgUi.Connect"]
  }
}
```

Field semantics:

| Field | Meaning |
|---|---|
| `agentName` | Canonical platform agent name |
| `agentVersion` | Version selected by the control plane for this bootstrap |
| `sessionId` | Platform-controlled session identifier |
| `runtimeSessionId` | Runtime-facing session identifier used for AG-UI session continuity |
| `startedAt` | UTC timestamp when the bootstrap/session was accepted |
| `expiresAt` | UTC timestamp when the SPA must refresh or keep alive the session contract |
| `transport` | AG-UI transport selected for the agent, currently `sse` or `websocket` |
| `connectUrl` | Runtime URL the SPA connects to after bootstrap |
| `tokenRefreshPath` | Platform BFF path for refreshing constrained AG-UI auth material |
| `sessionKeepalivePath` | Platform BFF path for extending session liveness |
| `auth.type` | Auth model for the returned connection material; target-state is `oauth2_obo` |
| `auth.audience` | Platform audience used when building AG-UI scopes; nullable only when the platform audience is intentionally absent in local/dev scenarios |
| `auth.scopeNames` | Scope names without audience prefix |
| `auth.scopes` | Full scopes approved for the AG-UI bootstrap |

## Error Contract

Target-state error surface:

| Status | Code | Meaning |
|---|---|---|
| `400` | `BAD_REQUEST` or `INVALID_REQUEST` | malformed JSON, invalid reconnect fields, or invalid bootstrap input |
| `401` | `UNAUTHORIZED` | caller identity missing or invalid |
| `403` | `FORBIDDEN` | tenant tier, capability policy, or agent access check failed |
| `404` | `NOT_FOUND` | agent not found or agent is not AG-UI enabled |
| `409` | `CONFLICT` | bootstrap request conflicts with session/runtime state |
| `500` | `INTERNAL_ERROR` | contract assembly or persistence failed |

Error bodies follow the platform JSON error shape:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Agent 'echo-agent' is not AG-UI enabled"
  }
}
```

## Audit Semantics

Successful bootstrap is intended to persist a session record in
`platform-sessions`.

Minimum session record semantics:

| Attribute | Meaning |
|---|---|
| `PK = TENANT#{tenantId}` | Tenant partition |
| `SK = SESSION#{sessionId}` | Platform session identity |
| `tenant_id` | Tenant ID |
| `app_id` | App ID |
| `session_id` | Platform session ID |
| `runtime_session_id` | Runtime session ID |
| `agent_name` | Agent associated with the bootstrap |
| `transport` | AG-UI transport |
| `connect_url` | Runtime URL returned to the SPA |
| `bootstrap_type = ag_ui` | Distinguishes AG-UI bootstrap records from other session records |
| `started_at` | UTC bootstrap timestamp |
| `last_activity_at` | Last known activity timestamp when available |
| `status` | Session lifecycle status when available |

Required audit behavior:
- a successful bootstrap must not return `200` unless the session record is durably accepted
- the control plane remains the audit boundary for AG-UI session start
- BFF token refresh and session keepalive operate on the platform session identity, not an opaque browser-only token cache

## Current Implementation Note

Current code already supports:
- `POST /v1/agents/{agentName}/bootstrap`
- `sessionId`
- `runtimeSessionId`
- `transport`
- `connectUrl`
- `tokenRefreshPath`
- `sessionKeepalivePath`
- `auth.scopes`
- session audit persistence to `platform-sessions`

Fields such as `agentVersion`, `startedAt`, `expiresAt`, `auth.type`, and
`auth.scopeNames` are part of the published target-state contract and must be
treated as required for completion of the AG-UI bootstrap feature set.
