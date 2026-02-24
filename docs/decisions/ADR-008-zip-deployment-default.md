# ADR-008: ZIP Deployment as Default, Container as Opt-In

## Status: Accepted
## Date: 2026-02-24

## Context
Agent code can be deployed to AgentCore Runtime as a Docker container (pushed to ECR)
or as a direct code deployment (ZIP file). Container builds take 5–10 minutes.
Fast inner loops require sub-30-second pushes on the warm path.

## Decision
ZIP deployment is the default. pyproject.toml [tool.agentcore.deployment.type] = "zip".
Container deployment available for agents with native binary dependencies,
set deployment.type = "container".

Warm path (dependencies unchanged): hash check passes, zip agent code only, ~15 seconds.
Cold path (dependencies changed): uv cross-compile arm64, zip all, ~90 seconds.
Hash is SHA256 of [project.dependencies] canonical serialisation, stored in SSM.

## Consequences
- Default inner loop: <30 seconds warm, <2 minutes cold
- No Docker required for most agent development
- Container deployment available when needed (native binaries, custom system packages)
- arm64 cross-compilation via uv covers >99% of PyPI packages
- Dependency caching in AgentCore Runtime reduces cold start latency

## Alternatives Rejected
- Container-only: 5–10 minute builds destroy the inner loop; Docker required locally
- No caching: rebuilding dependencies on every push is wasteful and slow
