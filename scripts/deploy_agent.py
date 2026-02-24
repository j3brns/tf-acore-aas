"""
deploy_agent.py — Deploy agent code to AgentCore Runtime.

For zip deployment (default):
    Calls AgentCore Runtime create/update API with code_zip containing
    s3_bucket, deps_key (from SSM), and script_key.

For container deployment (opt-in):
    Builds Docker image with --platform linux/arm64, pushes to ECR.

Deployment type is read from [tool.agentcore.deployment.type] in pyproject.toml.

Usage:
    uv run python scripts/deploy_agent.py <agent_name> --env <env>

Implemented in TASK-035.
ADRs: ADR-005, ADR-008
"""
