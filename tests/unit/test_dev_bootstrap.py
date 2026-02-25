"""
tests/unit/test_dev_bootstrap.py — Tests for scripts/dev-bootstrap.py

Validates:
  - All DynamoDB tables are created on first run
  - Tenant, agent, and tool fixture records are seeded correctly
  - SSM parameters are seeded correctly
  - .env.test is written with expected keys and values
  - Idempotency: running twice produces no duplicate records (same item counts)
  - JWT fetch failure: bootstrap still completes, .env.test written with empty JWTs

Test requirement (TASK-015): run twice, verify no duplicate records.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws


def _load_bootstrap_module() -> object:
    """Load scripts/dev-bootstrap.py as a Python module via importlib."""
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "dev_bootstrap", repo_root / "scripts" / "dev-bootstrap.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


bootstrap = _load_bootstrap_module()

_REGION = "eu-west-2"
_EXPECTED_TABLE_NAMES = {d["TableName"] for d in bootstrap.TABLE_DEFINITIONS}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_aws_clients() -> tuple[object, object, object]:
    """Return moto-backed DynamoDB client, DynamoDB resource, and SSM client."""
    ddb_client = boto3.client("dynamodb", region_name=_REGION)
    ddb_resource = boto3.resource("dynamodb", region_name=_REGION)
    ssm_client = boto3.client("ssm", region_name=_REGION)
    return ddb_client, ddb_resource, ssm_client


def _scan_table(ddb_resource: object, table_name: str) -> list[dict]:
    """Scan all items from a DynamoDB table and return them."""
    return ddb_resource.Table(table_name).scan()["Items"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------


@mock_aws
def test_ensure_tables_creates_all_tables() -> None:
    ddb_client, _, _ = _make_aws_clients()
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    tables = set(ddb_client.list_tables()["TableNames"])  # type: ignore[union-attr]
    assert _EXPECTED_TABLE_NAMES <= tables


@mock_aws
def test_ensure_tables_is_idempotent() -> None:
    """Calling ensure_tables twice must not raise and must not duplicate tables."""
    ddb_client, _, _ = _make_aws_clients()
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    tables = [t for t in ddb_client.list_tables()["TableNames"] if t.startswith("platform-")]  # type: ignore[union-attr]
    assert len(tables) == len(bootstrap.TABLE_DEFINITIONS)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tenant seeding
# ---------------------------------------------------------------------------


@mock_aws
def test_seed_tenants_creates_two_records() -> None:
    ddb_client, ddb_resource, _ = _make_aws_clients()
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    bootstrap.seed_tenants(ddb_resource)  # type: ignore[attr-defined]
    items = _scan_table(ddb_resource, "platform-tenants")
    assert len(items) == 2


@mock_aws
def test_seed_tenants_correct_tiers() -> None:
    ddb_client, ddb_resource, _ = _make_aws_clients()
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    bootstrap.seed_tenants(ddb_resource)  # type: ignore[attr-defined]
    items = {item["tenant_id"]: item for item in _scan_table(ddb_resource, "platform-tenants")}
    assert items["t-basic-001"]["tier"] == "basic"
    assert items["t-premium-001"]["tier"] == "premium"


@mock_aws
def test_seed_tenants_both_active() -> None:
    ddb_client, ddb_resource, _ = _make_aws_clients()
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    bootstrap.seed_tenants(ddb_resource)  # type: ignore[attr-defined]
    items = _scan_table(ddb_resource, "platform-tenants")
    assert all(item["status"] == "active" for item in items)


@mock_aws
def test_seed_tenants_idempotent_no_duplicates() -> None:
    """Running seed_tenants twice must not create duplicate records."""
    ddb_client, ddb_resource, _ = _make_aws_clients()
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    bootstrap.seed_tenants(ddb_resource)  # type: ignore[attr-defined]
    bootstrap.seed_tenants(ddb_resource)  # type: ignore[attr-defined]
    items = _scan_table(ddb_resource, "platform-tenants")
    assert len(items) == 2


# ---------------------------------------------------------------------------
# Agent seeding
# ---------------------------------------------------------------------------


@mock_aws
def test_seed_agents_creates_echo_agent() -> None:
    ddb_client, ddb_resource, _ = _make_aws_clients()
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    bootstrap.seed_agents(ddb_resource)  # type: ignore[attr-defined]
    items = _scan_table(ddb_resource, "platform-agents")
    assert len(items) == 1
    assert items[0]["agent_name"] == "echo-agent"
    assert items[0]["version"] == "1.0.0"


@mock_aws
def test_seed_agents_idempotent_no_duplicates() -> None:
    ddb_client, ddb_resource, _ = _make_aws_clients()
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    bootstrap.seed_agents(ddb_resource)  # type: ignore[attr-defined]
    bootstrap.seed_agents(ddb_resource)  # type: ignore[attr-defined]
    items = _scan_table(ddb_resource, "platform-agents")
    assert len(items) == 1


# ---------------------------------------------------------------------------
# Tool seeding
# ---------------------------------------------------------------------------


@mock_aws
def test_seed_tools_creates_echo_tool() -> None:
    ddb_client, ddb_resource, _ = _make_aws_clients()
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    bootstrap.seed_tools(ddb_resource)  # type: ignore[attr-defined]
    items = _scan_table(ddb_resource, "platform-tools")
    assert len(items) == 1
    assert items[0]["tool_name"] == "echo"


@mock_aws
def test_seed_tools_idempotent_no_duplicates() -> None:
    ddb_client, ddb_resource, _ = _make_aws_clients()
    bootstrap.ensure_tables(ddb_client)  # type: ignore[attr-defined]
    bootstrap.seed_tools(ddb_resource)  # type: ignore[attr-defined]
    bootstrap.seed_tools(ddb_resource)  # type: ignore[attr-defined]
    items = _scan_table(ddb_resource, "platform-tools")
    assert len(items) == 1


# ---------------------------------------------------------------------------
# SSM parameter seeding
# ---------------------------------------------------------------------------


@mock_aws
def test_seed_ssm_runtime_region() -> None:
    _, _, ssm_client = _make_aws_clients()
    bootstrap.seed_ssm_parameters(  # type: ignore[attr-defined]
        ssm_client,
        mock_jwks_url="http://localhost:8766",
        localstack_endpoint="http://localhost:4566",
    )
    resp = ssm_client.get_parameter(Name="/platform/config/runtime-region")  # type: ignore[union-attr]
    assert resp["Parameter"]["Value"] == "eu-west-1"


@mock_aws
def test_seed_ssm_jwks_url() -> None:
    _, _, ssm_client = _make_aws_clients()
    bootstrap.seed_ssm_parameters(  # type: ignore[attr-defined]
        ssm_client,
        mock_jwks_url="http://localhost:8766",
        localstack_endpoint="http://localhost:4566",
    )
    resp = ssm_client.get_parameter(Name="/platform/config/jwks-url")  # type: ignore[union-attr]
    assert resp["Parameter"]["Value"] == "http://localhost:8766/.well-known/jwks.json"


@mock_aws
def test_seed_ssm_api_audience() -> None:
    _, _, ssm_client = _make_aws_clients()
    bootstrap.seed_ssm_parameters(  # type: ignore[attr-defined]
        ssm_client,
        mock_jwks_url="http://localhost:8766",
        localstack_endpoint="http://localhost:4566",
    )
    resp = ssm_client.get_parameter(Name="/platform/config/api-audience")  # type: ignore[union-attr]
    assert resp["Parameter"]["Value"] == "api://platform-local"


@mock_aws
def test_seed_ssm_pii_patterns_is_valid_json() -> None:
    _, _, ssm_client = _make_aws_clients()
    bootstrap.seed_ssm_parameters(  # type: ignore[attr-defined]
        ssm_client,
        mock_jwks_url="http://localhost:8766",
        localstack_endpoint="http://localhost:4566",
    )
    resp = ssm_client.get_parameter(Name="/platform/gateway/pii-patterns/default")  # type: ignore[union-attr]
    patterns = json.loads(resp["Parameter"]["Value"])
    assert isinstance(patterns, list)
    assert len(patterns) > 0


@mock_aws
def test_seed_ssm_idempotent() -> None:
    """Seeding SSM parameters twice must not raise."""
    _, _, ssm_client = _make_aws_clients()
    for _ in range(2):
        bootstrap.seed_ssm_parameters(  # type: ignore[attr-defined]
            ssm_client,
            mock_jwks_url="http://localhost:8766",
            localstack_endpoint="http://localhost:4566",
        )
    resp = ssm_client.get_parameter(Name="/platform/config/env")  # type: ignore[union-attr]
    assert resp["Parameter"]["Value"] == "local"


# ---------------------------------------------------------------------------
# JWT fetching
# ---------------------------------------------------------------------------


def test_fetch_jwts_returns_empty_when_service_unavailable() -> None:
    """When mock-jwks is not reachable, fetch_jwts must return an empty dict."""
    import urllib.error

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("Connection refused"),
    ):
        tokens = bootstrap.fetch_jwts("http://127.0.0.1:19997")  # type: ignore[attr-defined]
    assert tokens == {}


def test_fetch_jwts_returns_three_tokens_when_service_available() -> None:
    """Mock the HTTP call and verify all three roles receive a token."""

    def _make_response() -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = json.dumps(
            {"access_token": "mock-jwt", "token_type": "Bearer", "expires_in": 86400}
        ).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    with patch("urllib.request.urlopen", side_effect=[_make_response() for _ in range(3)]):
        tokens = bootstrap.fetch_jwts("http://localhost:8766")  # type: ignore[attr-defined]

    assert set(tokens.keys()) == {"basic", "premium", "admin"}
    assert all(v == "mock-jwt" for v in tokens.values())


# ---------------------------------------------------------------------------
# .env.test writing
# ---------------------------------------------------------------------------


def test_write_env_test_with_tokens(tmp_path: Path) -> None:
    env_test_path = tmp_path / ".env.test"
    tokens = {"basic": "jwt-basic", "premium": "jwt-premium", "admin": "jwt-admin"}
    bootstrap.write_env_test(tokens, env_test_path)  # type: ignore[attr-defined]
    content = env_test_path.read_text()
    assert "TEST_JWT_BASIC=jwt-basic" in content
    assert "TEST_JWT_PREMIUM=jwt-premium" in content
    assert "TEST_JWT_ADMIN=jwt-admin" in content
    assert "AWS_REGION=eu-west-2" in content
    assert "LOCALSTACK_ENDPOINT=http://localhost:4566" in content


def test_write_env_test_without_tokens_writes_empty_values(tmp_path: Path) -> None:
    env_test_path = tmp_path / ".env.test"
    bootstrap.write_env_test({}, env_test_path)  # type: ignore[attr-defined]
    content = env_test_path.read_text()
    assert "TEST_JWT_BASIC=" in content
    assert "TEST_JWT_PREMIUM=" in content
    assert "mock-jwks service was not running" in content


def test_write_env_test_is_idempotent(tmp_path: Path) -> None:
    """Writing .env.test twice must produce stable output (no appending)."""
    env_test_path = tmp_path / ".env.test"
    tokens = {"basic": "jwt-b", "premium": "jwt-p", "admin": "jwt-a"}
    bootstrap.write_env_test(tokens, env_test_path)  # type: ignore[attr-defined]
    first_content = env_test_path.read_text()
    bootstrap.write_env_test(tokens, env_test_path)  # type: ignore[attr-defined]
    second_content = env_test_path.read_text()
    assert first_content == second_content


# ---------------------------------------------------------------------------
# Full end-to-end idempotency (TASK-015 requirement: run twice, no duplicates)
# ---------------------------------------------------------------------------


@mock_aws
def test_run_bootstrap_full_first_run(tmp_path: Path) -> None:
    """run_bootstrap produces all expected records on first run."""
    import urllib.error

    ddb_client = boto3.client("dynamodb", region_name=_REGION)
    ddb_resource = boto3.resource("dynamodb", region_name=_REGION)
    ssm_client = boto3.client("ssm", region_name=_REGION)
    env_test_path = tmp_path / ".env.test"

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("Connection refused"),
    ):
        bootstrap.run_bootstrap(  # type: ignore[attr-defined]
            ddb_client=ddb_client,
            ddb_resource=ddb_resource,
            ssm_client=ssm_client,
            env_test_path=env_test_path,
        )

    assert len(_scan_table(ddb_resource, "platform-tenants")) == 2
    assert len(_scan_table(ddb_resource, "platform-agents")) == 1
    assert len(_scan_table(ddb_resource, "platform-tools")) == 1
    assert env_test_path.exists()


@mock_aws
def test_run_bootstrap_twice_no_duplicate_records(tmp_path: Path) -> None:
    """TASK-015 acceptance: run twice, verify no duplicate records in any table."""
    import urllib.error

    ddb_client = boto3.client("dynamodb", region_name=_REGION)
    ddb_resource = boto3.resource("dynamodb", region_name=_REGION)
    ssm_client = boto3.client("ssm", region_name=_REGION)
    env_test_path = tmp_path / ".env.test"

    run_kwargs = {
        "ddb_client": ddb_client,
        "ddb_resource": ddb_resource,
        "ssm_client": ssm_client,
        "env_test_path": env_test_path,
    }

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("Connection refused"),
    ):
        bootstrap.run_bootstrap(**run_kwargs)  # type: ignore[attr-defined]
        counts_after_first = {
            "tenants": len(_scan_table(ddb_resource, "platform-tenants")),
            "agents": len(_scan_table(ddb_resource, "platform-agents")),
            "tools": len(_scan_table(ddb_resource, "platform-tools")),
        }

        bootstrap.run_bootstrap(**run_kwargs)  # type: ignore[attr-defined]
        counts_after_second = {
            "tenants": len(_scan_table(ddb_resource, "platform-tenants")),
            "agents": len(_scan_table(ddb_resource, "platform-agents")),
            "tools": len(_scan_table(ddb_resource, "platform-tools")),
        }

    assert counts_after_second == counts_after_first
    assert counts_after_second["tenants"] == 2
    assert counts_after_second["agents"] == 1
    assert counts_after_second["tools"] == 1
