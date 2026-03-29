# ADR-018: AgentCore AG-UI as an Additive Per-Agent Interactive Surface

## Status: Proposed
## Date: 2026-03-27

## Context
The platform currently exposes tenant-facing and operator-facing agent invocation
through the REST control plane:
- CloudFront and REST API Gateway form the northbound boundary
- Authoriser Lambda validates Entra JWTs and establishes tenant context
- Bridge Lambda performs tenant execution-role assumption, runtime region
  selection, and invocation logging
- SPA clients use the platform REST API for sync, SSE streaming, and async jobs
- BFF is intentionally thin and handles token refresh and session keepalive only

Amazon Bedrock AgentCore Runtime now supports the Agent User Interface (AG-UI)
protocol for direct agent-to-user interactive experiences using SSE or WebSocket.
AG-UI is attractive for richer UI experiences, but a direct browser-to-runtime
path would bypass existing platform control points unless the platform adds a
bootstrap and policy layer in front of it.

The repository also carries two hard constraints that shape the design:
- No Cognito anywhere; human auth remains Entra ID OIDC/JWT
- The existing REST bridge remains the canonical public API for E2B integrations and
  machine integrations

## Decision
The platform will treat AgentCore AG-UI as an additive interactive surface for
human-facing SPA experiences only.

The design has four parts:

1. **Keep the current REST bridge as the canonical public API**
   - REST remains the supported path for machine callers, async jobs, polling,
     webhooks, and non-UI integrations
   - Existing bridge-based invocation, failover, and audit semantics remain in force

2. **Introduce a platform-controlled AG-UI bootstrap path**
   - The SPA does not connect to AG-UI runtimes blindly
   - A platform endpoint validates Entra identity, tenant context, agent access,
     and session intent before returning AG-UI connection details
   - The platform records audit and observability metadata for AG-UI session start
     and end

3. **Deploy AG-UI per agent, not as a shared UI runtime**
   - Each AG-UI-capable agent owns its own runtime surface and release cadence
   - Agent registry metadata must declare whether AG-UI is supported and how the
     SPA discovers the runtime surface
   - Agents without AG-UI support continue using the existing REST invoke flow

4. **Prefer bootstrap-mediated auth over direct browser reuse of the existing
   Entra token**
   - The platform remains the policy and audit boundary
   - Any runtime-facing token or connection material given to the SPA must be
     constrained to the approved tenant, agent, and session scope

## Consequences
- AG-UI can be added without replacing the current REST invocation model
- Human interactive experiences can evolve independently from machine/API contracts
- The platform preserves tenant-aware audit, authorization, and session controls
- Per-agent runtimes avoid introducing a shared cross-agent UI bottleneck
- Additional control-plane work is required for AG-UI bootstrap, registry
  metadata, and SPA capability detection
- AG-UI deployment requires container-based runtime support and must not be
  silently folded into the existing ZIP-based default path

## Implementation Notes
- AG-UI is for human SPA sessions, not for generic tenant API clients
- The SPA must choose between:
  - the existing REST invoke flow, or
  - the AG-UI flow for agents explicitly marked AG-UI capable
- The thin BFF decision in ADR-011 remains valid for the current invoke path.
  AG-UI bootstrap may live in the control plane or in a narrowly-scoped BFF
  extension, but it must not become a second generic invoke router
- Existing bridge responsibilities such as runtime-region failover and invocation
  ledgering must either remain on the REST path or be explicitly reproduced for
  AG-UI sessions where required

## Alternatives Rejected
- **Replace the REST bridge with AG-UI for all agent traffic**: breaks the current
  public API contract, mixes human UX concerns with machine integrations, and
  bypasses established control-plane responsibilities
- **Single shared AG-UI runtime for all agents**: creates cross-agent coupling,
  release coordination overhead, and a larger blast radius for interactive changes
- **Direct browser-to-runtime auth as the primary model**: weakens control-plane
  enforcement, auditability, and tenant-scoped policy checks
- **Use Cognito for AG-UI because AWS examples do**: violates ADR-002 and the
  platform’s explicit identity constraints
