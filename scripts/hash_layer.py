"""
hash_layer.py — Dependency hash checker for agent layer caching.

Reads [project.dependencies] from the agent's pyproject.toml, computes a
canonical SHA256 hash, and compares against the stored hash in SSM.

Exit codes:
    0  Hash matches — dependencies unchanged, use warm push path (~15s)
    1  Hash mismatch — dependencies changed, rebuild required (~90s)

Hash algorithm:
    - Read [project.dependencies] list
    - Canonicalise: sort, strip whitespace
    - SHA256 of canonical form
    - First 16 hex characters

Usage:
    uv run python scripts/hash_layer.py <agent_name> --env <env>

Implemented in TASK-033.
ADRs: ADR-006, ADR-008
"""
