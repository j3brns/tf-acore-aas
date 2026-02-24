# ADR-004: Act-on-Behalf Identity Propagation (not Impersonation)

## Status: Accepted
## Date: 2026-02-24

## Context
When an agent invokes a tool via the Gateway, the tool Lambda needs to know who
is calling and on behalf of whom. Two options: impersonation (forward the original JWT)
or act-on-behalf (issue a new scoped token at each hop).

## Decision
Act-on-behalf at every hop. The REQUEST interceptor Lambda issues a new short-lived JWT
scoped to the specific tool being invoked. The original user JWT never reaches a tool Lambda.

## Consequences
- Prevents confused deputy attacks
- Reduces blast radius if a tool Lambda is compromised
- Each tool receives minimum required identity context
- Enables full auditability: each token hop is logged independently
- Requires token exchange logic in the REQUEST interceptor (additional complexity)
- Scoped tokens expire in 5 minutes — suitable for tool invocations

## Alternatives Rejected
- Impersonation (forwarding original JWT): if a tool Lambda is compromised, the attacker
  has the full user token with all permissions; blast radius is maximum
- No identity propagation: tools cannot authorise who is calling them
