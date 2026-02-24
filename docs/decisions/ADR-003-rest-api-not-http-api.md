# ADR-003: REST API Gateway over HTTP API

## Status: Accepted
## Date: 2026-02-24

## Context
November 2025 AWS release enabled response streaming on REST API Gateway. HTTP API
was previously the only option for streaming but lacks usage plans and per-method throttling.

## Decision
Use REST API Gateway (not HTTP API) for the northbound API surface.

## Consequences
- Usage plans provide per-tenant rate limiting natively — no application-level throttling needed
- Per-method throttle configurable — different limits per route
- WAF association supported natively on REST API
- Response streaming works (November 2025 capability)
- Lambda authorisers with result caching supported
- Slightly higher baseline cost than HTTP API — acceptable for the capability set

## Alternatives Rejected
- HTTP API: no usage plans, no per-method throttle, no WAF association
- WebSocket API: considered for streaming but Fetch + ReadableStream over REST API
  is simpler and sufficient for the SPA use case
