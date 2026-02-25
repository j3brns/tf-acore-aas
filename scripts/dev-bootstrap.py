"""
dev-bootstrap.py — Local development environment seeding script.

Seeds LocalStack with:
  - Two test tenants: t-basic-001 (basic tier) and t-premium-001 (premium tier)
  - All platform SSM parameters pointing to LocalStack endpoints
  - DynamoDB table fixtures (tenants, agents, tools)
  - Test JWTs written to .env.test

Idempotent — safe to run multiple times.  Running twice produces the same
set of records; no duplicates are created.

Usage:
    uv run python scripts/dev-bootstrap.py

Called automatically by: make dev

Implemented in TASK-015.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Repository root (used for .env.test default path)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# DynamoDB table names
# ---------------------------------------------------------------------------

_TABLE_NAMES = [
    "platform-tenants",
    "platform-agents",
    "platform-invocations",
    "platform-jobs",
    "platform-sessions",
    "platform-tools",
    "platform-ops-locks",
]

# ---------------------------------------------------------------------------
# Fixture constants — stable IDs for local dev test fixtures
# ---------------------------------------------------------------------------

_BASIC_TENANT_ID = "t-basic-001"
_BASIC_APP_ID = "app-basic-001"
_PREMIUM_TENANT_ID = "t-premium-001"
_PREMIUM_APP_ID = "app-premium-001"
_ECHO_AGENT_NAME = "echo-agent"
_ECHO_AGENT_VERSION = "0.1.0"
# All-zeros account ID used exclusively for local dev fixtures (not production)
_LOCAL_DEV_ACCOUNT = "000000000000"

# ---------------------------------------------------------------------------
# Static SSM parameters (not including the dynamically generated jwt-secret)
# ---------------------------------------------------------------------------

_STATIC_SSM_PARAMS: dict[str, str] = {
    "/platform/config/runtime-region": "eu-west-1",
    "/platform/config/fallback-region": "eu-central-1",
    "/platform/config/environment": "local",
    "/platform/auth/jwks-url": "http://localhost:8766/.well-known/jwks.json",
    "/platform/auth/entra-audience": "api://platform-local",
    "/platform/auth/entra-issuer": (
        "https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0"
    ),
    "/platform/gateway/pii-patterns/default": json.dumps(
        [
            r"\b[A-Z]{2}\d{6}[A-Z]\b",  # UK NI number
            r"\b\d{3}\s\d{3}\s\d{4}\b",  # UK NHS number
            r"\b\d{2}-\d{2}-\d{2}\b",  # UK sort code
            r"\b\d{8}\b",  # UK bank account number
            r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b",  # email
        ]
    ),
}

_JWT_SECRET_PARAM = "/platform/local/jwt-secret"
_ENTRA_ISSUER = _STATIC_SSM_PARAMS["/platform/auth/entra-issuer"]
_ENTRA_AUDIENCE = _STATIC_SSM_PARAMS["/platform/auth/entra-audience"]


# ---------------------------------------------------------------------------
# AWS client helpers — region and endpoint always read from environment
# ---------------------------------------------------------------------------


def _get_region() -> str:
    """Read AWS region from environment.  Fails loudly if not set."""
    return os.environ["AWS_REGION"]


def _get_endpoint() -> str | None:
    """Read LocalStack endpoint from environment.

    Returns the explicit endpoint URL when LOCALSTACK_ENDPOINT is set
    (routes requests to a running LocalStack instance).

    Returns None when the variable is absent so that boto3 uses its default
    service endpoint.  This allows moto to intercept calls in unit tests
    without a real LocalStack process running.
    """
    return os.environ.get("LOCALSTACK_ENDPOINT") or None


def _ssm_client() -> Any:
    """Create an SSM boto3 client, routing to LocalStack when LOCALSTACK_ENDPOINT is set."""
    return boto3.client("ssm", region_name=_get_region(), endpoint_url=_get_endpoint())


def _dynamodb_resource() -> Any:
    """Create a DynamoDB boto3 resource, routing to LocalStack when LOCALSTACK_ENDPOINT is set."""
    return boto3.resource("dynamodb", region_name=_get_region(), endpoint_url=_get_endpoint())


# ---------------------------------------------------------------------------
# DynamoDB table creation
# ---------------------------------------------------------------------------


def _create_tables(dynamodb: Any) -> None:
    """Create all platform DynamoDB tables. Idempotent — skips existing tables.

    All tables share the same key schema (PK string hash key + SK string range key)
    matching the platform single-table design convention.
    """
    for table_name in _TABLE_NAMES:
        try:
            dynamodb.create_table(
                TableName=table_name,
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            _log(f"  [+] created table {table_name}")
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code", "")
            if code in ("ResourceInUseException", "TableAlreadyExistsException"):
                _log(f"  [=] table {table_name} already exists")
            else:
                raise


# ---------------------------------------------------------------------------
# DynamoDB item helpers
# ---------------------------------------------------------------------------


def _put_if_absent(table: Any, item: dict[str, Any]) -> bool:
    """Write item only if PK does not already exist.

    Uses a conditional put so that repeated runs never overwrite existing
    records, preserving createdAt timestamps and any manual edits made during
    a dev session.

    Returns True if the item was written, False if it already existed.
    """
    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK)",
        )
        return True
    except ClientError as exc:
        if (exc.response.get("Error") or {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Tenant seeding
# ---------------------------------------------------------------------------


def _seed_tenants(dynamodb: Any) -> None:
    """Seed basic and premium test tenants."""
    table = dynamodb.Table("platform-tenants")
    now = _now_iso()

    tenants: list[dict[str, Any]] = [
        {
            "PK": f"TENANT#{_BASIC_TENANT_ID}",
            "SK": "METADATA",
            "tenantId": _BASIC_TENANT_ID,
            "appId": _BASIC_APP_ID,
            "displayName": "Test Tenant Basic",
            "tier": "basic",
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
            "ownerEmail": "basic-tenant@example.com",
            "ownerTeam": "platform-test",
            "accountId": _LOCAL_DEV_ACCOUNT,
            "runtimeRegion": "eu-west-1",
            "fallbackRegion": "eu-central-1",
            "monthlyBudgetUsd": Decimal("100.00"),
        },
        {
            "PK": f"TENANT#{_PREMIUM_TENANT_ID}",
            "SK": "METADATA",
            "tenantId": _PREMIUM_TENANT_ID,
            "appId": _PREMIUM_APP_ID,
            "displayName": "Test Tenant Premium",
            "tier": "premium",
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
            "ownerEmail": "premium-tenant@example.com",
            "ownerTeam": "platform-test",
            "accountId": _LOCAL_DEV_ACCOUNT,
            "runtimeRegion": "eu-west-1",
            "fallbackRegion": "eu-central-1",
            "monthlyBudgetUsd": Decimal("5000.00"),
        },
    ]

    for tenant in tenants:
        created = _put_if_absent(table, tenant)
        mark = "+" if created else "="
        action = "created" if created else "exists"
        _log(f"  [{mark}] tenant {tenant['tenantId']} ({action})")


# ---------------------------------------------------------------------------
# Agent seeding
# ---------------------------------------------------------------------------


def _seed_agents(dynamodb: Any) -> None:
    """Seed the echo-agent reference fixture."""
    table = dynamodb.Table("platform-agents")
    now = _now_iso()
    region = _get_region()

    agent: dict[str, Any] = {
        "PK": f"AGENT#{_ECHO_AGENT_NAME}",
        "SK": f"VERSION#{_ECHO_AGENT_VERSION}",
        "agentName": _ECHO_AGENT_NAME,
        "version": _ECHO_AGENT_VERSION,
        "ownerTeam": "platform-core",
        "tierMinimum": "basic",
        "layerHash": "0000000000000000",
        "layerS3Key": f"layers/{_ECHO_AGENT_NAME}/{_ECHO_AGENT_VERSION}/deps.zip",
        "scriptS3Key": f"agents/{_ECHO_AGENT_NAME}/{_ECHO_AGENT_VERSION}/code.zip",
        "deployedAt": now,
        "invocationMode": "sync",
        "streamingEnabled": True,
        "estimatedDurationSeconds": 5,
        # Placeholder runtime ARN — overwritten by make agent-push after TASK-024
        "runtimeArn": (
            f"arn:aws:bedrock:{region}:{_LOCAL_DEV_ACCOUNT}:agent/{_ECHO_AGENT_NAME}-local"
        ),
    }

    created = _put_if_absent(table, agent)
    mark = "+" if created else "="
    action = "created" if created else "exists"
    _log(f"  [{mark}] agent {_ECHO_AGENT_NAME} v{_ECHO_AGENT_VERSION} ({action})")


# ---------------------------------------------------------------------------
# Tool seeding
# ---------------------------------------------------------------------------


def _seed_tools(dynamodb: Any) -> None:
    """Seed platform tool registry fixtures."""
    table = dynamodb.Table("platform-tools")
    region = _get_region()

    tools: list[dict[str, Any]] = [
        {
            "PK": "TOOL#web-search",
            "SK": "GLOBAL",
            "toolName": "web-search",
            "tierMinimum": "basic",
            "lambdaArn": (
                f"arn:aws:lambda:{region}:{_LOCAL_DEV_ACCOUNT}:function:platform-web-search-local"
            ),
            "gatewayTargetId": "web-search-local",
            "enabled": True,
        },
        {
            "PK": "TOOL#code-interpreter",
            "SK": "GLOBAL",
            "toolName": "code-interpreter",
            "tierMinimum": "premium",
            "lambdaArn": (
                f"arn:aws:lambda:{region}:{_LOCAL_DEV_ACCOUNT}"
                ":function:platform-code-interpreter-local"
            ),
            "gatewayTargetId": "code-interpreter-local",
            "enabled": True,
        },
    ]

    for tool in tools:
        created = _put_if_absent(table, tool)
        mark = "+" if created else "="
        action = "created" if created else "exists"
        _log(f"  [{mark}] tool {tool['toolName']} ({action})")


# ---------------------------------------------------------------------------
# SSM parameter seeding
# ---------------------------------------------------------------------------


def _get_or_create_jwt_secret(ssm: Any) -> str:
    """Return the local dev JWT signing secret from SSM; generate and store if absent.

    The secret is persisted in SSM so that repeated bootstrap runs produce the
    same JWTs (allowing .env.test to remain stable across restarts).
    """
    try:
        response = ssm.get_parameter(Name=_JWT_SECRET_PARAM)
        _log(f"  [=] {_JWT_SECRET_PARAM} (exists)")
        return response["Parameter"]["Value"]
    except ClientError as exc:
        if (exc.response.get("Error") or {}).get("Code") != "ParameterNotFound":
            raise

    new_secret = secrets.token_hex(32)
    ssm.put_parameter(
        Name=_JWT_SECRET_PARAM,
        Value=new_secret,
        Type="String",
        Description="Local dev JWT signing secret — NOT for production use",
    )
    _log(f"  [+] {_JWT_SECRET_PARAM} (generated)")
    return new_secret


def _seed_ssm_params(ssm: Any) -> None:
    """Seed all static platform SSM parameters.

    Uses Overwrite=True so the values are always refreshed to the canonical
    local dev defaults on each bootstrap run.  This is safe because these are
    all known constant values for the local environment.
    """
    endpoint = _get_endpoint() or "http://localhost:4566"
    params = {
        **_STATIC_SSM_PARAMS,
        "/platform/local/localstack-endpoint": endpoint,
    }

    for name, value in params.items():
        ssm.put_parameter(
            Name=name,
            Value=value,
            Type="String",
            Overwrite=True,
        )
        _log(f"  [=] {name}")


# ---------------------------------------------------------------------------
# JWT helpers (HS256 — local dev only, NOT for production)
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_test_jwt(claims: dict[str, Any], secret: str) -> str:
    """Build a minimal HS256 JWT for local development testing.

    The resulting token has the same structural claims as an Entra-issued JWT
    so that the authoriser Lambda (TASK-016) can be configured to validate it
    in local mode using the shared secret from SSM /platform/local/jwt-secret.
    """
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


def _build_test_claims(
    *,
    tenant_id: str,
    app_id: str,
    tier: str,
    sub: str,
) -> dict[str, Any]:
    now = int(datetime.now(UTC).timestamp())
    exp = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
    return {
        "sub": sub,
        "aud": _ENTRA_AUDIENCE,
        "iss": _ENTRA_ISSUER,
        "iat": now,
        "exp": exp,
        "tenantid": tenant_id,
        "appid": app_id,
        "tier": tier,
        "roles": ["Agent.Invoke"],
    }


# ---------------------------------------------------------------------------
# .env.test writer
# ---------------------------------------------------------------------------


def _write_env_test(jwt_secret: str, env_test_path: Path) -> None:
    """Write test tenant credentials to .env.test.

    The file is regenerated on every bootstrap run so the JWTs always
    reflect the current secret and claim state.  .env.test is gitignored.
    """
    basic_jwt = _make_test_jwt(
        _build_test_claims(
            tenant_id=_BASIC_TENANT_ID,
            app_id=_BASIC_APP_ID,
            tier="basic",
            sub="test-basic@example.com",
        ),
        jwt_secret,
    )
    premium_jwt = _make_test_jwt(
        _build_test_claims(
            tenant_id=_PREMIUM_TENANT_ID,
            app_id=_PREMIUM_APP_ID,
            tier="premium",
            sub="test-premium@example.com",
        ),
        jwt_secret,
    )

    endpoint = _get_endpoint() or "http://localhost:4566"
    content = (
        "# Generated by scripts/dev-bootstrap.py — do not edit manually\n"
        f"BASIC_TENANT_ID={_BASIC_TENANT_ID}\n"
        f"BASIC_APP_ID={_BASIC_APP_ID}\n"
        f"BASIC_TENANT_JWT={basic_jwt}\n"
        f"PREMIUM_TENANT_ID={_PREMIUM_TENANT_ID}\n"
        f"PREMIUM_APP_ID={_PREMIUM_APP_ID}\n"
        f"PREMIUM_TENANT_JWT={premium_jwt}\n"
        f"AWS_REGION={_get_region()}\n"
        f"LOCALSTACK_ENDPOINT={endpoint}\n"
    )
    env_test_path.write_text(content)
    _log(f"  [=] wrote {env_test_path}")


# ---------------------------------------------------------------------------
# Output helper
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def run(*, env_test_path: Path | None = None) -> None:
    """Seed the local development environment.

    Idempotent — safe to call multiple times without creating duplicate records
    or overwriting manually edited tenant data.

    Args:
        env_test_path: Override the default .env.test path.  Used in tests.
    """
    if env_test_path is None:
        env_test_path = _REPO_ROOT / ".env.test"

    _log("==> dev-bootstrap: seeding local environment")

    dynamodb = _dynamodb_resource()
    ssm = _ssm_client()

    _log("--- DynamoDB tables")
    _create_tables(dynamodb)

    _log("--- Tenants")
    _seed_tenants(dynamodb)

    _log("--- Agents")
    _seed_agents(dynamodb)

    _log("--- Tools")
    _seed_tools(dynamodb)

    _log("--- SSM parameters")
    jwt_secret = _get_or_create_jwt_secret(ssm)
    _seed_ssm_params(ssm)

    _log("--- .env.test")
    _write_env_test(jwt_secret, env_test_path)

    _log("==> dev-bootstrap: complete")


def main() -> None:
    """CLI entrypoint with structured error reporting."""
    try:
        run()
    except KeyError as exc:
        print(f"ERROR: required environment variable {exc} is not set", file=sys.stderr)
        print("  Hint: export AWS_REGION=eu-west-2", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
