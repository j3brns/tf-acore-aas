"""Wait for local development dependencies to become healthy."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.request import urlopen


@dataclass(frozen=True)
class ServiceCheck:
    name: str
    url: str


LOCAL_DEV_SERVICES = (
    ServiceCheck("LocalStack", "http://localhost:4566/_localstack/health"),
    ServiceCheck("mock runtime", "http://localhost:8765/ping"),
    ServiceCheck("mock JWKS", "http://localhost:8766/health"),
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
            if hasattr(response, "__enter__"):
                with response:
                    return
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        wait_for_all_services(
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
    except TimeoutError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
