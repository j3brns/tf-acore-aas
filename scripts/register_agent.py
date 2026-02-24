"""
register_agent.py — Register or update agent in the platform registry.

Writes agent metadata to DynamoDB platform-agents table and SSM.
Reads [tool.agentcore] manifest from agent's pyproject.toml.

Registered attributes: agentName, version, ownerTeam, tierMinimum,
layerHash, layerS3Key, scriptS3Key, runtimeArn, deployedAt,
invocationMode, streamingEnabled, estimatedDurationSeconds.

Usage:
    uv run python scripts/register_agent.py <agent_name> --env <env>

Implemented in TASK-035.
ADRs: ADR-005, ADR-008
"""
