# ADR-011: Thin BFF for Token Refresh and Session Keepalive Only

## Status: Accepted
## Date: 2026-02-24

## Context
The SPA needs to handle token refresh during long streaming sessions. AgentCore Runtime
destroys sessions after 15 minutes of inactivity. Both require server-side coordination.

## Decision
A thin BFF Lambda handles two specific concerns:
1. Token refresh: Entra on-behalf-of flow to refresh expired tokens during active sessions
2. Session keepalive: periodic ping to AgentCore Runtime to prevent idle timeout

All agent invocations go directly via the bridge Lambda. The BFF never handles invocations.

## Consequences
- SPA auth model remains simple (MSAL.js handles standard flows)
- Long streaming sessions survive token expiry without interruption
- Sessions survive idle periods during user think time
- One additional Lambda (small, low-traffic)
- BFF uses Entra OBO flow — requires specific app registration configuration

## Alternatives Rejected
- Full BFF: duplicates invocation routing, adds a second latency hop on the hot path
- No BFF: streaming sessions interrupted by token expiry; sessions destroyed after 15min
  idle during long agent reasoning (user sees blank results with no error)
- MSAL.js popup during streaming: disruptive UX, race condition with stream rendering
