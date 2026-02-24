"""
build_layer.py — Build and upload arm64 dependency layer for an agent.

Cross-compiles Python dependencies for aarch64-manylinux2014 (AgentCore Runtime).
Uploads to S3 and updates SSM hash and s3-key.

Command:
    uv pip install \\
        --python-platform aarch64-manylinux2014 \\
        --python-version 3.12 \\
        --target=.build/deps \\
        --only-binary=:all:

--only-binary=:all: ensures no source packages are compiled on the wrong arch.

Usage:
    uv run python scripts/build_layer.py <agent_name> --env <env>

Implemented in TASK-034.
ADRs: ADR-006
"""
