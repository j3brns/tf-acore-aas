# Documentation Suite

This folder is the canonical documentation suite for `tf-acore-aas`.
Use it as the entry point for architecture, operations, security, and implementation decisions.

## Start Here

- [`ARCHITECTURE.md`](ARCHITECTURE.md): system topology, request lifecycle, tenant isolation, scaling, and failure modes.
- [`decisions/`](decisions/): architecture decision records (ADR-001..013).
- [`operations/`](operations/): operator runbooks and incident procedures.
- [`security/`](security/): threat model and compliance checks.
- [`development/`](development/): local setup and agent developer guidance.
- [`TASKS.md`](TASKS.md): legacy task snapshot (GitHub Issues are canonical).

## Diagram Catalog

All diagrams live in [`docs/images/`](images/) with `.drawio` as source-of-truth and matching `.drawio.svg` + `.drawio.png` exports.

| Diagram | Audience | Purpose |
|---|---|---|
| [`tf_acore_aas_architecture.drawio.svg`](images/tf_acore_aas_architecture.drawio.svg) | Standard | Canonical platform architecture across regions and planes. |
| [`tf_acore_aas_architecture_engineer.drawio.svg`](images/tf_acore_aas_architecture_engineer.drawio.svg) | Engineer | Detailed architecture with explicit service interactions. |
| [`tf_acore_aas_architecture_exec.drawio.svg`](images/tf_acore_aas_architecture_exec.drawio.svg) | Executive | Simplified architecture emphasizing business-risk controls. |
| [`tf_acore_aas_request_lifecycle_engineer.drawio.svg`](images/tf_acore_aas_request_lifecycle_engineer.drawio.svg) | Engineer | End-to-end auth, runtime execution, async webhook path, and failover. |
| [`tf_acore_aas_cdk_stack_dependencies.drawio.svg`](images/tf_acore_aas_cdk_stack_dependencies.drawio.svg) | Standard | CDK stack dependency order and deployment boundaries. |
| [`tf_acore_aas_cdk_dependencies_engineer.drawio.svg`](images/tf_acore_aas_cdk_dependencies_engineer.drawio.svg) | Engineer | Detailed code-level CDK dependency relationships. |
| [`tf_acore_aas_cdk_dependencies_exec.drawio.svg`](images/tf_acore_aas_cdk_dependencies_exec.drawio.svg) | Executive | Simplified CDK dependency view for planning and governance. |
| [`tf_acore_aas_entities_state_diagram.drawio.svg`](images/tf_acore_aas_entities_state_diagram.drawio.svg) | Standard | Core platform entities and lifecycle/state transitions. |

For source editing, open the `.drawio` files in the same directory.

## Diagram Semantics

Color semantics used across architecture diagrams:

- Blue edges: request/control-plane request flow
- Green edges: runtime execution flow
- Purple edges: async/event flow
- Amber dashed edges: operational/failover/inferred relationship

## Validation Commands

Use these when diagrams are changed:

```bash
for f in docs/images/*.drawio; do drawio -x -f svg -e -o "${f}.svg" "$f"; drawio -x -f png -e -b 10 -o "${f}.png" "$f"; done
for f in docs/images/*.drawio; do c=$(rg -o "resIcon=mxgraph.aws4" "$f" | wc -l); echo "$(basename "$f") $c"; done
ls docs/images/*.drawio | wc -l; ls docs/images/*.drawio.svg | wc -l; ls docs/images/*.drawio.png | wc -l
```
