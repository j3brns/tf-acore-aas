# ADR-006: uv and pyproject.toml for Python Dependency Management

## Status: Accepted
## Date: 2026-02-24

## Context
Python dependencies must be cross-compiled for arm64 (aarch64-manylinux2014) for
AgentCore Runtime. Fast dependency resolution is critical for the inner loop.

## Decision
uv for all Python dependency management. pyproject.toml as single file merging
project dependencies and agent manifest via [tool.agentcore] namespace. uv.lock
for reproducible builds.

Cross-compilation command:
uv pip install --python-platform aarch64-manylinux2014 --python-version 3.12
--target=.build/deps --only-binary=:all:

## Consequences
- Dependency resolution 10–100x faster than pip
- uv.lock ensures reproducible builds across environments
- arm64 cross-compilation native in uv — no Docker required for dependency building
- [tool.agentcore] namespace keeps agent manifest co-located with dependencies
- --only-binary=:all: rejects source-only packages (no native compilation risk on wrong arch)

## Alternatives Rejected
- pip: significantly slower resolution, no native arm64 cross-compile shortcut
- poetry: no --python-platform flag for cross-compilation
- requirements.txt: no lockfile semantics, separate from project metadata
