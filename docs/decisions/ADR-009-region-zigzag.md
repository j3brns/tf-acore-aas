# ADR-009: eu-west-2 London Home, eu-west-1 Dublin Runtime

## Status: Accepted
## Date: 2026-02-24

## Status Note (2026-03-10)
AWS now offers AgentCore Runtime and Policy in eu-west-2 (London). This ADR remains
the active platform deployment policy until a successor ADR explicitly approves a
migration away from the current London-home / Dublin-runtime zigzag topology.

## Context
At the time of this decision, AgentCore Runtime was not available in eu-west-2
(London). All data had to remain in the EU, and compliance required UK/EU data
residency.

## Decision
eu-west-2 London: home region for all data, control plane, and application services.
eu-west-1 Dublin: AgentCore Runtime compute (12ms RTT from London).
eu-central-1 Frankfurt: AgentCore Evaluations and runtime failover target.

Runtime failover path: Dublin (primary) → Frankfurt (fallback).
Failover controlled by SSM /platform/config/runtime-region.
Distributed lock in DynamoDB prevents multiple bridge Lambda instances racing on failover.

## Consequences
- ~12ms additional RTT for every agent invocation (Dublin round-trip)
- All data remains in London — GDPR/UK ICO compliance maintained
- Dual-region operational complexity managed by bridge Lambda and runbooks
- Failover is application-level (SSM parameter), not DNS-level
- When London Runtime support changes, topology changes still require an explicit
  architecture review rather than silent drift in docs or deployment defaults

## Alternatives Rejected
- Everything in Frankfurt: data residency concerns for UK tenants post-Brexit
- Everything in Dublin: data plane in a non-home region; compliance risk
- Wait for eu-west-2 AgentCore support: no committed GA date; blocking is not viable
