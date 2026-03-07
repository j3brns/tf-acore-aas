"""
dev-bootstrap.py — Local development environment seeding script.

Seeds LocalStack with:
  - Two test tenants: basic-tier (t-basic-001) and premium-tier (t-premium-001)
  - All SSM parameters pointing to LocalStack endpoints
  - DynamoDB tables with fixture data (tables created if missing)
  - Test JWTs written to .env.test

Idempotent — safe to run multiple times.  Running twice produces the same
set of records; no duplicates are created.

Usage:
    uv run python scripts/dev-bootstrap.py

Called automatically by: make dev

Implemented in TASK-015.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("dev-bootstrap")

# ---------------------------------------------------------------------------
# Defaults (overridable via environment variables at call sites)
# ---------------------------------------------------------------------------
_DEFAULT_LOCALSTACK_ENDPOINT = "http://localhost:4566"
_DEFAULT_MOCK_JWKS_URL = "http://localhost:8766"
_DEFAULT_REGION = "eu-west-2"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ENV_TEST_PATH = _REPO_ROOT / ".env.test"

# ---------------------------------------------------------------------------
# DynamoDB table definitions
# ---------------------------------------------------------------------------
TABLE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "TableName": "platform-tenants",
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "platform-agents",
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "platform-invocations",
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "platform-jobs",
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "platform-sessions",
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "platform-tools",
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "platform-ops-locks",
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
]

# ---------------------------------------------------------------------------
# Fixture data — deterministic keys, stable timestamps
# ---------------------------------------------------------------------------
TENANT_FIXTURES: list[dict[str, Any]] = [
    {
        "PK": "TENANT#t-basic-001",
        "SK": "METADATA",
        "tenant_id": "t-basic-001",
        "app_id": "platform-local",
        "display_name": "Test Tenant Basic",
        "tier": "basic",
        "status": "active",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "owner_email": "basic-test@example.local",
        "owner_team": "platform-test",
        "account_id": "000000000000",
        "monthly_budget_usd": Decimal("100"),
    },
    {
        "PK": "TENANT#t-premium-001",
        "SK": "METADATA",
        "tenant_id": "t-premium-001",
        "app_id": "platform-local",
        "display_name": "Test Tenant Premium",
        "tier": "premium",
        "status": "active",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "owner_email": "premium-test@example.local",
        "owner_team": "platform-test",
        "account_id": "000000000000",
        "monthly_budget_usd": Decimal("1000"),
    },
]

AGENT_FIXTURES: list[dict[str, Any]] = [
    {
        "PK": "AGENT#echo-agent",
        "SK": "VERSION#1.0.0",
        "agent_name": "echo-agent",
        "version": "1.0.0",
        "owner_team": "platform-test",
        "tier_minimum": "basic",
        "layer_hash": "0000000000000000",
        "layer_s3_key": "layers/echo-agent/1.0.0-0000000000000000.zip",
        "script_s3_key": "scripts/echo-agent/1.0.0.zip",
        "deployed_at": "2026-01-01T00:00:00+00:00",
        "invocation_mode": "sync",
        "streaming_enabled": True,
    },
]

TOOL_FIXTURES: list[dict[str, Any]] = [
    {
        "PK": "TOOL#echo",
        "SK": "GLOBAL",
        "tool_name": "echo",
        "tier_minimum": "basic",
        "lambda_arn": ("arn:aws:lambda:eu-west-2:000000000000:function:platform-echo-tool-local"),
        "gateway_target_id": "echo-target-local",
        "enabled": True,
    },
]


def _ssm_parameters(*, mock_jwks_url: str, localstack_endpoint: str) -> list[tuple[str, str]]:
    """Return (name, value) pairs for all platform SSM parameters."""
    pii_patterns = json.dumps(
        [
            r"\b[A-Z]{2}\d{6}[A-D]\b",  # UK NI number
            r"\b\d{3}[-\s]\d{3}[-\s]\d{4}\b",  # NHS number (simplified)
            r"\b\d{2}-\d{2}-\d{2}\b",  # Sort code
            r"\b\d{8}\b",  # Bank account number
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",  # Email
        ]
    )
    return [
        ("/platform/config/runtime-region", "eu-west-1"),
        ("/platform/config/fallback-region", "eu-central-1"),
        ("/platform/config/jwks-url", f"{mock_jwks_url}/.well-known/jwks.json"),
        ("/platform/config/api-audience", "api://platform-local"),
        ("/platform/config/api-issuer", mock_jwks_url),
        ("/platform/config/env", "local"),
        ("/platform/config/mock-runtime-url", "http://localhost:8765"),
        ("/platform/config/localstack-endpoint", localstack_endpoint),
        ("/platform/gateway/pii-patterns/default", pii_patterns),
    ]


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------


def ensure_tables(ddb_client: Any) -> None:
    """Create DynamoDB tables if they do not already exist. Idempotent."""
    for defn in TABLE_DEFINITIONS:
        table_name = defn["TableName"]
        try:
            ddb_client.create_table(**defn)
            logger.info("Created table %s", table_name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("ResourceInUseException", "TableAlreadyExistsException"):
                logger.debug("Table %s already exists — skipping", table_name)
            else:
                raise


def _seed_items(ddb_resource: Any, table_name: str, items: list[dict[str, Any]]) -> None:
    """Upsert items into a DynamoDB table. put_item is idempotent by PK+SK key."""
    table = ddb_resource.Table(table_name)
    for item in items:
        table.put_item(Item=item)
    logger.info("Seeded %d item(s) into %s", len(items), table_name)


def seed_tenants(ddb_resource: Any) -> None:
    """Upsert two test tenant records (basic-tier and premium-tier)."""
    _seed_items(ddb_resource, "platform-tenants", TENANT_FIXTURES)


def seed_agents(ddb_resource: Any) -> None:
    """Upsert echo-agent v1.0.0 fixture record."""
    _seed_items(ddb_resource, "platform-agents", AGENT_FIXTURES)


def seed_tools(ddb_resource: Any) -> None:
    """Upsert echo tool fixture record."""
    _seed_items(ddb_resource, "platform-tools", TOOL_FIXTURES)


def seed_ssm_parameters(ssm_client: Any, *, mock_jwks_url: str, localstack_endpoint: str) -> None:
    """Upsert all platform SSM parameters. Overwrites existing values."""
    params = _ssm_parameters(mock_jwks_url=mock_jwks_url, localstack_endpoint=localstack_endpoint)
    for name, value in params:
        ssm_client.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)
    logger.info("Seeded %d SSM parameter(s)", len(params))


def fetch_jwts(mock_jwks_url: str) -> dict[str, str]:
    """Fetch test JWTs from the mock-jwks service.

    Issues three tokens: basic, premium, and admin (Platform.Admin role).
    Returns an empty dict if the service is unavailable — caller handles gracefully.
    """
    requests_to_make: list[tuple[str, dict[str, Any]]] = [
        (
            "basic",
            {"tenant_id": "t-basic-001", "app_id": "platform-local", "tier": "basic", "ttl": 86400},
        ),
        (
            "premium",
            {
                "tenant_id": "t-premium-001",
                "app_id": "platform-local",
                "tier": "premium",
                "ttl": 86400,
            },
        ),
        (
            "admin",
            {
                "tenant_id": "t-basic-001",
                "app_id": "platform-local",
                "tier": "basic",
                "sub": "admin-user",
                "roles": ["Platform.Admin"],
                "ttl": 86400,
            },
        ),
    ]
    tokens: dict[str, str] = {}
    for role, payload in requests_to_make:
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{mock_jwks_url}/token",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
                tokens[role] = body["access_token"]
        except (urllib.error.URLError, OSError, KeyError) as exc:
            logger.warning("Could not fetch JWT for role=%s: %s", role, exc)
    return tokens


def write_env_test(tokens: dict[str, str], env_test_path: Path) -> None:
    """Write test environment variables to .env.test. Overwrites on each run."""
    lines: list[str] = [
        "# Test environment variables — generated by scripts/dev-bootstrap.py",
        "# Re-run `make bootstrap` (with dev services up) to refresh JWTs",
        "#",
        "AWS_REGION=eu-west-2",
        "LOCALSTACK_ENDPOINT=http://localhost:4566",
        "MOCK_RUNTIME_URL=http://localhost:8765",
        "JWKS_URL=http://localhost:8766/.well-known/jwks.json",
        "API_AUDIENCE=api://platform-local",
        "API_ISSUER=http://localhost:8766",
        "",
    ]
    if tokens:
        lines += [
            f"TEST_JWT_BASIC={tokens.get('basic', '')}",
            f"TEST_JWT_PREMIUM={tokens.get('premium', '')}",
            f"TEST_JWT_ADMIN={tokens.get('admin', '')}",
        ]
    else:
        lines += [
            "# JWTs not available (mock-jwks service was not running)",
            "TEST_JWT_BASIC=",
            "TEST_JWT_PREMIUM=",
            "TEST_JWT_ADMIN=",
        ]
    env_test_path.write_text("\n".join(lines) + "\n")
    logger.info("Written %s", env_test_path)


def run_bootstrap(
    *,
    localstack_endpoint: str = _DEFAULT_LOCALSTACK_ENDPOINT,
    mock_jwks_url: str = _DEFAULT_MOCK_JWKS_URL,
    aws_region: str = _DEFAULT_REGION,
    env_test_path: Path = _DEFAULT_ENV_TEST_PATH,
    ddb_client: Any = None,
    ddb_resource: Any = None,
    ssm_client: Any = None,
) -> None:
    """Run the full dev bootstrap sequence.

    AWS client parameters may be injected for testing (moto-backed clients).
    When None, real boto3 clients are constructed pointing at localstack_endpoint.
    """
    # LocalStack accepts any credential values; read from env for CI compatibility.
    _ls_key = os.environ.get("AWS_ACCESS_KEY_ID", "test")  # pragma: allowlist secret
    _ls_secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "test")  # pragma: allowlist secret
    boto_kwargs: dict[str, Any] = {
        "region_name": aws_region,
        "endpoint_url": localstack_endpoint,
        "aws_access_key_id": _ls_key,
        "aws_secret_access_key": _ls_secret,
    }
    _ddb_client: Any = ddb_client or boto3.client("dynamodb", **boto_kwargs)
    _ddb_resource: Any = ddb_resource or boto3.resource("dynamodb", **boto_kwargs)
    _ssm_client: Any = ssm_client or boto3.client("ssm", **boto_kwargs)

    logger.info("==> dev-bootstrap starting (localstack=%s)", localstack_endpoint)

    logger.info("-- Step 1: Ensure DynamoDB tables")
    ensure_tables(_ddb_client)

    logger.info("-- Step 2: Seed tenants")
    seed_tenants(_ddb_resource)

    logger.info("-- Step 3: Seed agents")
    seed_agents(_ddb_resource)

    logger.info("-- Step 4: Seed tools")
    seed_tools(_ddb_resource)

    logger.info("-- Step 5: Seed SSM parameters")
    seed_ssm_parameters(
        _ssm_client,
        mock_jwks_url=mock_jwks_url,
        localstack_endpoint=localstack_endpoint,
    )

    logger.info("-- Step 6: Fetch test JWTs")
    tokens = fetch_jwts(mock_jwks_url)

    logger.info("-- Step 7: Write .env.test")
    write_env_test(tokens, env_test_path)

    logger.info("==> dev-bootstrap complete")


if __name__ == "__main__":
    _localstack = os.environ.get("LOCALSTACK_ENDPOINT", _DEFAULT_LOCALSTACK_ENDPOINT)
    _mock_jwks = os.environ.get("MOCK_JWKS_URL", _DEFAULT_MOCK_JWKS_URL)
    _region = os.environ.get("AWS_REGION", _DEFAULT_REGION)
    _env_test = Path(os.environ.get("ENV_TEST_PATH", str(_DEFAULT_ENV_TEST_PATH)))
    try:
        run_bootstrap(
            localstack_endpoint=_localstack,
            mock_jwks_url=_mock_jwks,
            aws_region=_region,
            env_test_path=_env_test,
        )
    except Exception:
        logger.exception("dev-bootstrap failed")
        sys.exit(1)
