"""
tests/unit/test_dev_bootstrap.py — Idempotency tests for dev-bootstrap.py.

Key assertion (per TASK-015 spec):
    Running dev-bootstrap.py twice must produce the same set of records
    with no duplicates.

All AWS calls are intercepted by moto's mock_aws context.  No LocalStack
instance is required.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------


def _load_bootstrap() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "dev_bootstrap",
        repo_root / "scripts" / "dev-bootstrap.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["dev_bootstrap"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def bootstrap_module() -> Any:
    """Load dev-bootstrap.py once for the test module."""
    return _load_bootstrap()


# ---------------------------------------------------------------------------
# Environment fixture — sets required env vars for every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject the environment variables that dev-bootstrap.py requires.

    LOCALSTACK_ENDPOINT is deliberately NOT set here so that boto3 uses its
    default endpoint URL — this allows moto's mock_aws to intercept all calls
    without a real LocalStack process.  The production dev flow sets
    LOCALSTACK_ENDPOINT=http://localhost:4566 via .env.local / Makefile.
    """
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
    monkeypatch.delenv("LOCALSTACK_ENDPOINT", raising=False)


# ---------------------------------------------------------------------------
# Helper — scan all items from a DynamoDB table via the mocked resource
# ---------------------------------------------------------------------------


def _scan_table(table_name: str) -> list[dict[str, Any]]:
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-2")
    table = dynamodb.Table(table_name)
    return table.scan()["Items"]


# ---------------------------------------------------------------------------
# Idempotency tests — the core TASK-015 requirement
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Running dev-bootstrap twice must not create duplicate DynamoDB records."""

    def test_no_duplicate_tenants(self, bootstrap_module: Any, tmp_path: Path) -> None:
        """Exactly two tenant records exist after two bootstrap runs."""
        env_test = tmp_path / ".env.test"
        with mock_aws():
            bootstrap_module.run(env_test_path=env_test)
            bootstrap_module.run(env_test_path=env_test)

            items = _scan_table("platform-tenants")

        pks = {item["PK"] for item in items}
        assert pks == {"TENANT#t-basic-001", "TENANT#t-premium-001"}
        assert len(items) == 2, f"Expected 2 tenant records, got {len(items)}"

    def test_no_duplicate_agents(self, bootstrap_module: Any, tmp_path: Path) -> None:
        """Exactly one agent record exists after two bootstrap runs."""
        env_test = tmp_path / ".env.test"
        with mock_aws():
            bootstrap_module.run(env_test_path=env_test)
            count_after_first = len(_scan_table("platform-agents"))

            bootstrap_module.run(env_test_path=env_test)
            count_after_second = len(_scan_table("platform-agents"))

        assert count_after_first == count_after_second
        assert count_after_first == 1

    def test_no_duplicate_tools(self, bootstrap_module: Any, tmp_path: Path) -> None:
        """Exactly two tool records exist after two bootstrap runs."""
        env_test = tmp_path / ".env.test"
        with mock_aws():
            bootstrap_module.run(env_test_path=env_test)
            count_after_first = len(_scan_table("platform-tools"))

            bootstrap_module.run(env_test_path=env_test)
            count_after_second = len(_scan_table("platform-tools"))

        assert count_after_first == count_after_second
        assert count_after_first == 2

    def test_jwt_secret_stable_across_runs(self, bootstrap_module: Any, tmp_path: Path) -> None:
        """The JWT signing secret is created once and reused on subsequent runs.

        If a new secret were generated on every run the JWTs in .env.test
        would change on each bootstrap, breaking any in-flight test sessions.
        """
        env_test = tmp_path / ".env.test"
        with mock_aws():
            bootstrap_module.run(env_test_path=env_test)
            jwt_first = (tmp_path / ".env.test").read_text()

            bootstrap_module.run(env_test_path=env_test)
            jwt_second = (tmp_path / ".env.test").read_text()

        # The JWT values in .env.test must be identical between runs
        def _extract_jwt(content: str, key: str) -> str:
            for line in content.splitlines():
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1]
            raise AssertionError(f"{key} not found in .env.test")

        assert _extract_jwt(jwt_first, "BASIC_TENANT_JWT") == _extract_jwt(
            jwt_second, "BASIC_TENANT_JWT"
        )
        assert _extract_jwt(jwt_first, "PREMIUM_TENANT_JWT") == _extract_jwt(
            jwt_second, "PREMIUM_TENANT_JWT"
        )


# ---------------------------------------------------------------------------
# Table creation tests
# ---------------------------------------------------------------------------


class TestTableCreation:
    """All required DynamoDB tables are created."""

    def test_all_tables_created(self, bootstrap_module: Any, tmp_path: Path) -> None:
        expected = {
            "platform-tenants",
            "platform-agents",
            "platform-invocations",
            "platform-jobs",
            "platform-sessions",
            "platform-tools",
            "platform-ops-locks",
        }
        with mock_aws():
            bootstrap_module.run(env_test_path=tmp_path / ".env.test")
            dynamodb = boto3.client("dynamodb", region_name="eu-west-2")
            response = dynamodb.list_tables()
            tables = set(response["TableNames"])

        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_table_creation_idempotent(self, bootstrap_module: Any, tmp_path: Path) -> None:
        """Running bootstrap twice must not raise on existing tables."""
        with mock_aws():
            bootstrap_module.run(env_test_path=tmp_path / ".env.test")
            # Second run must not raise ResourceInUseException
            bootstrap_module.run(env_test_path=tmp_path / ".env.test")


# ---------------------------------------------------------------------------
# Tenant fixture content tests
# ---------------------------------------------------------------------------


class TestTenantFixtures:
    """Seeded tenant records have the correct attributes."""

    def test_basic_tenant_attributes(self, bootstrap_module: Any, tmp_path: Path) -> None:
        with mock_aws():
            bootstrap_module.run(env_test_path=tmp_path / ".env.test")
            items = _scan_table("platform-tenants")

        basic = next(i for i in items if i["PK"] == "TENANT#t-basic-001")
        assert basic["SK"] == "METADATA"
        assert basic["tier"] == "basic"
        assert basic["status"] == "active"
        assert basic["tenantId"] == "t-basic-001"
        assert basic["appId"] == "app-basic-001"

    def test_premium_tenant_attributes(self, bootstrap_module: Any, tmp_path: Path) -> None:
        with mock_aws():
            bootstrap_module.run(env_test_path=tmp_path / ".env.test")
            items = _scan_table("platform-tenants")

        premium = next(i for i in items if i["PK"] == "TENANT#t-premium-001")
        assert premium["tier"] == "premium"
        assert premium["status"] == "active"
        assert premium["tenantId"] == "t-premium-001"


# ---------------------------------------------------------------------------
# SSM parameter tests
# ---------------------------------------------------------------------------


class TestSsmParameters:
    """Required SSM parameters are seeded with correct values."""

    def _get_param(self, name: str) -> str:
        ssm = boto3.client("ssm", region_name="eu-west-2")
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]

    def test_runtime_region_seeded(self, bootstrap_module: Any, tmp_path: Path) -> None:
        with mock_aws():
            bootstrap_module.run(env_test_path=tmp_path / ".env.test")
            value = self._get_param("/platform/config/runtime-region")
        assert value == "eu-west-1"

    def test_environment_seeded(self, bootstrap_module: Any, tmp_path: Path) -> None:
        with mock_aws():
            bootstrap_module.run(env_test_path=tmp_path / ".env.test")
            value = self._get_param("/platform/config/environment")
        assert value == "local"

    def test_jwks_url_points_to_mock(self, bootstrap_module: Any, tmp_path: Path) -> None:
        with mock_aws():
            bootstrap_module.run(env_test_path=tmp_path / ".env.test")
            value = self._get_param("/platform/auth/jwks-url")
        assert "localhost:8766" in value
        assert value.endswith("/.well-known/jwks.json")

    def test_jwt_secret_seeded(self, bootstrap_module: Any, tmp_path: Path) -> None:
        with mock_aws():
            bootstrap_module.run(env_test_path=tmp_path / ".env.test")
            value = self._get_param("/platform/local/jwt-secret")
        # Secret is a 64-char hex string (32 bytes)
        assert len(value) == 64
        assert all(c in "0123456789abcdef" for c in value)

    def test_localstack_endpoint_seeded(self, bootstrap_module: Any, tmp_path: Path) -> None:
        """The localstack endpoint SSM param is seeded (defaults to localhost:4566)."""
        with mock_aws():
            bootstrap_module.run(env_test_path=tmp_path / ".env.test")
            value = self._get_param("/platform/local/localstack-endpoint")
        # In test mode LOCALSTACK_ENDPOINT is not set; the seed function writes
        # the effective endpoint (None → default "http://localhost:4566")
        assert value  # non-empty


# ---------------------------------------------------------------------------
# .env.test content tests
# ---------------------------------------------------------------------------


class TestEnvTestFile:
    """The .env.test file has the required keys and well-formed values."""

    def _parse_env(self, path: Path) -> dict[str, str]:
        result = {}
        for line in path.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                result[key] = val
        return result

    def test_required_keys_present(self, bootstrap_module: Any, tmp_path: Path) -> None:
        env_test = tmp_path / ".env.test"
        with mock_aws():
            bootstrap_module.run(env_test_path=env_test)

        env = self._parse_env(env_test)
        required = {
            "BASIC_TENANT_ID",
            "BASIC_APP_ID",
            "BASIC_TENANT_JWT",
            "PREMIUM_TENANT_ID",
            "PREMIUM_APP_ID",
            "PREMIUM_TENANT_JWT",
            "AWS_REGION",
            "LOCALSTACK_ENDPOINT",
        }
        missing = required - set(env.keys())
        assert not missing, f"Missing keys in .env.test: {missing}"

    def test_tenant_ids_correct(self, bootstrap_module: Any, tmp_path: Path) -> None:
        env_test = tmp_path / ".env.test"
        with mock_aws():
            bootstrap_module.run(env_test_path=env_test)

        env = self._parse_env(env_test)
        assert env["BASIC_TENANT_ID"] == "t-basic-001"
        assert env["PREMIUM_TENANT_ID"] == "t-premium-001"

    def test_jwts_are_three_part_tokens(self, bootstrap_module: Any, tmp_path: Path) -> None:
        """JWTs have three base64url-encoded parts separated by dots."""
        env_test = tmp_path / ".env.test"
        with mock_aws():
            bootstrap_module.run(env_test_path=env_test)

        env = self._parse_env(env_test)
        for key in ("BASIC_TENANT_JWT", "PREMIUM_TENANT_JWT"):
            parts = env[key].split(".")
            assert len(parts) == 3, f"{key} is not a valid JWT (expected 3 parts)"

    def test_aws_region_written(self, bootstrap_module: Any, tmp_path: Path) -> None:
        env_test = tmp_path / ".env.test"
        with mock_aws():
            bootstrap_module.run(env_test_path=env_test)

        env = self._parse_env(env_test)
        assert env["AWS_REGION"] == "eu-west-2"
