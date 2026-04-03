"""Wait for local development dependencies to become healthy."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

import boto3


@dataclass(frozen=True)
class ServiceCheck:
    name: str
    url: str


LOCAL_DEV_SERVICES = (
    ServiceCheck("LocalStack", "http://localhost:4566/_localstack/health"),
    ServiceCheck("mock runtime", "http://localhost:8765/ping"),
    ServiceCheck("mock JWKS", "http://localhost:8766/health"),
)

DEFAULT_LOCALSTACK_ENDPOINT = "http://localhost:4566"
DEFAULT_AWS_REGION = "eu-west-2"
DEFAULT_ENV_TEST_PATH = Path(__file__).resolve().parents[1] / ".env.test"

REQUIRED_TABLES = (
    "platform-tenants",
    "platform-agents",
    "platform-invocations",
    "platform-jobs",
    "platform-sessions",
    "platform-tools",
    "platform-ops-locks",
)

REQUIRED_SSM_PARAMETERS = (
    "/platform/config/runtime-region",
    "/platform/config/fallback-region",
    "/platform/config/env",
    "/platform/config/jwks-url",
    "/platform/config/api-audience",
)

REQUIRED_ENV_TEST_KEYS = (
    "BASIC_TENANT_ID",
    "BASIC_TENANT_JWT",
    "PREMIUM_TENANT_ID",
    "PREMIUM_TENANT_JWT",
    "ADMIN_JWT",
)


def wait_for_service(
    check: ServiceCheck,
    *,
    timeout_seconds: int,
    interval_seconds: float,
    fetcher: Callable[[str, float], object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Poll a health endpoint until it responds or the timeout expires."""
    fetch = fetcher or (lambda url, timeout: urlopen(url, timeout=timeout))
    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None

    while time.monotonic() < deadline:
        try:
            response = fetch(check.url, interval_seconds)
            closer = getattr(response, "close", None)
            if callable(closer):
                closer()
            return
        except OSError as exc:
            last_error = str(exc)
        sleep(interval_seconds)

    detail = f" Last error: {last_error}" if last_error else ""
    raise TimeoutError(
        f"Timed out waiting for {check.name} at {check.url} after {timeout_seconds}s.{detail}"
    )


def wait_for_all_services(
    *,
    timeout_seconds: int,
    interval_seconds: float,
    checks: tuple[ServiceCheck, ...] = LOCAL_DEV_SERVICES,
) -> None:
    for check in checks:
        print(f"==> Waiting for {check.name} ({check.url})...")
        wait_for_service(
            check,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
        )


def verify_seeded_state(
    *,
    localstack_endpoint: str,
    aws_region: str,
    env_test_path: Path,
) -> None:
    """Fail if required local seeded state is missing after dev-bootstrap."""
    ddb = boto3.client(
        "dynamodb",
        region_name=aws_region,
        endpoint_url=localstack_endpoint,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "testing"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "testing"),
    )
    ssm = boto3.client(
        "ssm",
        region_name=aws_region,
        endpoint_url=localstack_endpoint,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "testing"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "testing"),
    )

    table_names = set(ddb.list_tables().get("TableNames", []))
    missing_tables = [name for name in REQUIRED_TABLES if name not in table_names]
    if missing_tables:
        raise RuntimeError(
            f"Local seeded state missing DynamoDB tables: {', '.join(missing_tables)}"
        )

    response = ssm.get_parameters(Names=list(REQUIRED_SSM_PARAMETERS))
    found = {item.get("Name") for item in response.get("Parameters", [])}
    missing_params = [name for name in REQUIRED_SSM_PARAMETERS if name not in found]
    if missing_params:
        raise RuntimeError(f"Local seeded state missing SSM params: {', '.join(missing_params)}")

    if not env_test_path.exists():
        raise RuntimeError(f"Local seeded state missing env file: {env_test_path}")

    values: dict[str, str] = {}
    for line in env_test_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    missing_env_keys = [key for key in REQUIRED_ENV_TEST_KEYS if values.get(key, "") == ""]
    if missing_env_keys:
        raise RuntimeError(
            f"Local seeded state missing env keys or values: {', '.join(missing_env_keys)}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wait until local development dependencies are healthy."
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=60,
        help="Maximum time to wait for each service",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=2.0,
        help="Polling interval between readiness checks",
    )
    parser.add_argument(
        "--check-seeded-state",
        action="store_true",
        help="Validate LocalStack tables, SSM parameters, and .env.test after dev-bootstrap",
    )
    parser.add_argument(
        "--localstack-endpoint",
        default=DEFAULT_LOCALSTACK_ENDPOINT,
        help="LocalStack endpoint used for seeded-state verification",
    )
    parser.add_argument(
        "--aws-region",
        default=DEFAULT_AWS_REGION,
        help="AWS region used for LocalStack seeded-state verification",
    )
    parser.add_argument(
        "--env-test-path",
        default=str(DEFAULT_ENV_TEST_PATH),
        help="Path to the .env.test file written by dev-bootstrap",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        wait_for_all_services(
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
        if args.check_seeded_state:
            verify_seeded_state(
                localstack_endpoint=args.localstack_endpoint,
                aws_region=args.aws_region,
                env_test_path=Path(args.env_test_path),
            )
    except TimeoutError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
