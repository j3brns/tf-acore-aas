# AI Coding Assistant Context

## Read First
Always read CLAUDE.md at the project root before starting any task.
Always read docs/ARCHITECTURE.md before changing infrastructure.
Always read the relevant ADR in docs/decisions/ before reversing a decision.
Run `make plan-dev TASK="description"` before implementing anything non-trivial.

## Task State
Current task list is in docs/TASKS.md.
Each task has a status: [ ] not started, [~] in progress, [x] done, [!] blocked.
State the task number when you start: "Starting TASK-016: authoriser Lambda"

## Key Principles
- This is a multi-tenant platform. EVERY operation must be tenant-scoped.
- appid and tenantid must appear on every log line, metric, and trace.
- data_access (src/data_access/) is the ONLY permitted DynamoDB interface.
  Never write raw boto3 DynamoDB calls in Lambda handlers.
- appid and tenantid on every log line, metric dimension, and trace annotation.
- AgentCore Runtime is arm64 only. All Python deps need arm64 cross-compilation.
- Authentication: Entra JWT for humans, SigV4 for machines. No Cognito.
- Invocation mode is DECLARED (sync|streaming|async), never inferred at runtime.
- Async mode uses app.add_async_task / app.complete_async_task — NOT SQS routing.

## When To Stop And Ask
- Any change to DynamoDB partition key or GSI design
- Any change to IAM policies or trust relationships
- Any change to authoriser Lambda validation logic
- Any new dependency adding >10MB to the deployment package
- Any change affecting tenant isolation in data_access
- Any change to KMS key policy
- Any operation touching production data

## Forbidden Patterns
See CLAUDE.md — Forbidden Patterns section.

## Architecture Decisions
ADR-001: AgentCore Runtime chosen over custom orchestration
ADR-002: Entra ID not Cognito
ADR-003: REST API not HTTP API
ADR-004: Act-on-behalf not impersonation
ADR-005: Declared invocation mode not runtime detection
ADR-006: uv and pyproject.toml
ADR-007: CDK + Terraform (not pure Terraform)
ADR-008: ZIP deployment default
ADR-009: eu-west-2 home, eu-west-1 Runtime
ADR-010: AgentCore native async not SQS routing
ADR-011: Thin BFF only
ADR-012: On-demand vs provisioned DynamoDB
ADR-013: Entra group-to-role claim for RBAC

## Draw.io Diagram Standards (Project)
- Canonical location: keep all architecture diagrams and generated assets in `docs/images/`.
- Canonical sources: `.drawio` is source-of-truth; always export matching `.drawio.svg` and `.drawio.png`.
- Naming convention: use `tf_acore_aas_<subject>[_<audience>].drawio` where audience is `engineer` or `exec` when applicable.
- Required views for major architecture updates:
  - Standard architecture: `tf_acore_aas_architecture.drawio`
  - Engineer detail view: `tf_acore_aas_architecture_engineer.drawio`
  - Executive view: `tf_acore_aas_architecture_exec.drawio`
- Layout discipline:
  - Use grid-aligned coordinates (multiples of 10 where practical).
  - Use swimlanes/containers for regions and planes; avoid free-floating clusters.
  - Min spacing: ~200px horizontal / ~120px vertical between major nodes.
- Visual semantics (must be consistent across diagrams):
  - Blue edges = request/control-plane request flow.
  - Green edges = runtime execution flow.
  - Purple edges = async/event flow.
  - Amber dashed edges = operational/failover/inferred relationship.
  - Include a legend box in each diagram encoding these semantics.
- AWS iconography policy:
  - Use AWS resource/role icons (`mxgraph.aws4`) for AWS services and roles, not generic boxes.
  - Use actor/user icons for external humans/clients; use role icons for IAM roles.
  - Minimum icon coverage targets:
    - architecture/architecture_engineer: >=20 icons
    - architecture_exec: >=12 icons
    - cdk_dependencies_engineer: >=15 icons
    - cdk_dependencies_exec: >=6 icons
    - cdk_stack_dependencies: >=6 icons
    - entities_state_diagram: >=12 icons
- Edge correctness requirements:
  - Prefer orthogonal routing with arrowhead clearance.
  - Distinguish explicit code dependencies from inferred operational sequencing in CDK diagrams.
  - Do not imply incorrect trust/assumption paths (for example authoriser directly assuming tenant roles).
- Quality loop (required for non-trivial updates):
  - Iterate up to 10 passes until layout coherence, flow correctness, and icon coverage checks pass.
  - Reject outputs that are dense, ambiguous, or icon-poor.

## Draw.io Export + Validation Commands
- Export all diagrams:
  - `for f in docs/images/*.drawio; do drawio -x -f svg -e -o "${f}.svg" "$f"; drawio -x -f png -e -b 10 -o "${f}.png" "$f"; done`
- Verify icon coverage quickly:
  - `for f in docs/images/*.drawio; do c=$(rg -o "resIcon=mxgraph.aws4" "$f" | wc -l); echo "$(basename "$f") $c"; done`
- Verify output completeness:
  - `ls docs/images/*.drawio | wc -l; ls docs/images/*.drawio.svg | wc -l; ls docs/images/*.drawio.png | wc -l`

## Draw.io Skill Additions (Apply When Extending Skill)
- Add a mandatory "AWS icon coverage gate" with per-diagram thresholds.
- Add a "flow semantics gate" requiring legend presence and color/line semantic consistency.
- Add a "CDK dependency truthfulness gate": explicit vs operational edges must be separated and labeled.
- Add an "audience-profiled output contract" for standard/engineer/exec variants.
- Add a "final export gate" that requires `.drawio`, `.svg`, and `.png` regeneration after source changes.
