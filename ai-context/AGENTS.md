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

## Issue Queue Hygiene
- GitHub Issues are the canonical queue; `docs/TASKS.md` is only a snapshot.
- Every task issue must have exactly one `status:*` label.
- Closed task issues must always be `status:done`; they must not retain `status:in-progress`, `status:not-started`, or `ready`.
- Use `make finish-worktree-close` as the required final close step even if the issue was already closed manually, because it normalizes lifecycle labels.
- Before declaring issue work complete, run `make issues-audit`. If it fails, run `make issues-reconcile` and re-audit until clean.

## Key Principles
- This is a multi-tenant platform. EVERY operation must be tenant-scoped.
- appid and tenantid must appear on every log line, metric, and trace.
- data-access-lib (src/data-access-lib/) is the ONLY permitted DynamoDB interface.
  Never write raw boto3 DynamoDB calls in Lambda handlers.
- AgentCore Runtime is arm64 only. All Python deps need arm64 cross-compilation.
- Authentication: Entra JWT for humans, SigV4 for machines. No Cognito.
- Invocation mode is DECLARED (sync|streaming|async), never inferred at runtime.
- Async mode uses app.add_async_task / app.complete_async_task — NOT SQS routing.

## When To Stop And Ask
- Any change to DynamoDB partition key or GSI design
- Any change to IAM policies or trust relationships
- Any change to authoriser Lambda validation logic
- Any new dependency adding >10MB to the deployment package
- Any change affecting tenant isolation in data-access-lib
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

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **wt266** (2202 symbols, 6793 relationships, 178 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/wt266/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/wt266/context` | Codebase overview, check index freshness |
| `gitnexus://repo/wt266/clusters` | All functional areas |
| `gitnexus://repo/wt266/processes` | All execution flows |
| `gitnexus://repo/wt266/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## CLI

- Re-index: `npx gitnexus analyze`
- Check freshness: `npx gitnexus status`
- Generate docs: `npx gitnexus wiki`

<!-- gitnexus:end -->
