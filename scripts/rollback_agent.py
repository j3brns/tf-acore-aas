"""
rollback_agent.py — Roll back an agent to its previous deployed version.

Queries the platform-agents DynamoDB table for the previous version,
re-deploys that version to AgentCore Runtime, and updates the registry.

Usage:
    uv run python scripts/rollback_agent.py <agent_name> --env <env>

Called by: make agent-rollback AGENT=<name> ENV=prod

Implemented in TASK-035.
"""
