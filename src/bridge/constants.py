from __future__ import annotations

import os
import re

# Table Names
TENANTS_TABLE = os.environ.get("TENANTS_TABLE", "platform-tenants")
AGENTS_TABLE = os.environ.get("AGENTS_TABLE", "platform-agents")
INVOCATIONS_TABLE = os.environ.get("INVOCATIONS_TABLE", "platform-invocations")
JOBS_TABLE = os.environ.get("JOBS_TABLE", "platform-jobs")
SESSIONS_TABLE = os.environ.get("SESSIONS_TABLE", "platform-sessions")
OPS_LOCKS_TABLE = os.environ.get("OPS_LOCKS_TABLE", "platform-ops-locks")
FAILOVER_LOCK_NAME = os.environ.get("FAILOVER_LOCK_NAME", "platform-runtime-failover")

# Environment & Infrastructure
JOB_RESULTS_BUCKET = os.environ.get("JOB_RESULTS_BUCKET")
ENTRA_AUDIENCE = os.environ.get("ENTRA_AUDIENCE")
AG_UI_SCOPE_NAME = os.environ.get("AG_UI_SCOPE_NAME", "Agent.AgUi.Connect")
BFF_TOKEN_REFRESH_PATH = "/v1/bff/token-refresh"
BFF_SESSION_KEEPALIVE_PATH = "/v1/bff/session-keepalive"

# SSM / AppConfig Paths
RUNTIME_REGION_PARAM = os.environ.get("RUNTIME_REGION_PARAM", "/platform/config/runtime-region")
MOCK_RUNTIME_URL_PARAM = os.environ.get(
    "MOCK_RUNTIME_URL_PARAM", "/platform/config/mock-runtime-url"
)
TENANT_EXECUTION_ROLE_PARAM_TEMPLATE = os.environ.get(
    "TENANT_EXECUTION_ROLE_PARAM_TEMPLATE", "/platform/tenants/{tenant_id}/execution-role-arn"
)

# Timeouts & TTLs
JOB_RESULT_URL_EXPIRY_SECONDS = int(os.environ.get("JOB_RESULT_URL_EXPIRY_SECONDS", "3600"))
AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS = int(
    os.environ.get("AGENTCORE_RUNTIME_CONNECT_TIMEOUT_SECONDS", "5")
)
AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS = int(
    os.environ.get("AGENTCORE_RUNTIME_READ_TIMEOUT_SECONDS", "900")
)
INVOCATION_TTL_SECONDS = 90 * 24 * 60 * 60
JOB_TTL_SECONDS = 7 * 24 * 60 * 60

# Regex Patterns
IAM_ROLE_ARN_PATTERN = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):iam::(?P<account_id>\d{12}):role/(?P<role_name>[\w+=,.@\-_/]+)$"
)
RUNTIME_ARN_PATTERN = re.compile(
    r"^arn:(?P<partition>aws|aws-us-gov|aws-cn):bedrock-agentcore:(?P<region>[a-z0-9-]+):"
    r"(?P<account_id>\d{12}):runtime/(?P<runtime_id>[\w+=,.@\-_/]+)$"
)

# Validation
VALID_WEBHOOK_EVENTS = {"job.completed", "job.failed"}
