# ADR-012: On-Demand for Invocations, Provisioned for Config Tables

## Status: Accepted
## Date: 2026-02-24

## Context
Platform-invocations table volume is unpredictable and tenant-driven. Config tables
(tenants, agents) have stable, low-volume access patterns.

## Decision
platform-invocations, platform-jobs, platform-sessions: on-demand capacity.
platform-tenants, platform-agents, platform-tools: provisioned with auto-scaling,
5 RCU/WCU minimum.
platform-ops-locks: provisioned, 1 RCU/WCU (very low volume operational table).

Hot partition mitigation on invocations: composite SK includes a random 2-character
jitter suffix for tenants exceeding 1000 requests/minute.

## Consequences
- Invocations never throttled by capacity planning errors
- Config tables cost ~$0.65/month at minimum (5 RCU/WCU) vs on-demand variability
- Auto-scaling on config tables handles unexpected spikes without on-demand pricing
- Jitter suffix prevents DynamoDB hot partition on single-tenant spikes

## Alternatives Rejected
- All on-demand: higher cost for stable access patterns on config tables
- All provisioned: invocation table capacity must be predicted — dangerous for a new platform
