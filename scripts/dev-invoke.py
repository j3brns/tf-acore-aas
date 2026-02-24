"""
dev-invoke.py — Invoke an agent in the local development environment.

Sends a request to the local mock AgentCore Runtime (docker-compose).
Reads tenant JWT from .env.test.

Usage:
    uv run python scripts/dev-invoke.py \\
        --agent <agent_name> \\
        --tenant <tenant_id> \\
        --jwt <jwt_token> \\
        --prompt "Hello" \\
        --mode sync|streaming|async \\
        [--env dev]

Called by: make dev-invoke, make agent-invoke

Implemented in TASK-015.
"""
