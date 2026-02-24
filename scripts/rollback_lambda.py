"""
rollback_lambda.py — Roll back a Lambda function to its previous alias version.

Updates the Lambda alias to point to the previous published version.
Used for emergency rollbacks without a full CDK deploy.

Usage:
    uv run python scripts/rollback_lambda.py <function_name> <env>

Example:
    uv run python scripts/rollback_lambda.py bridge prod

Called by: make infra-rollback-lambda FUNCTION=bridge ENV=prod

Implemented in TASK-028.
"""
