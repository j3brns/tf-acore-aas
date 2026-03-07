#!/usr/bin/env python3
"""
failover_lock.py — DynamoDB distributed lock for region failover.

Prevents race conditions when multiple operators/processes attempt
simultaneous runtime region failover.

Lock record:
  table: platform-ops-locks
  PK:    LOCK#platform-runtime-failover
  SK:    METADATA
TTL:
  5 minutes (auto-expire prevents permanent lock if operator disconnects)

Usage:
    uv run python scripts/failover_lock.py acquire --env <env>
    uv run python scripts/failover_lock.py release --env <env>

Implemented in TASK-030.
ADRs: ADR-009
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

DEFAULT_TABLE_NAME = "platform-ops-locks"
DEFAULT_LOCK_NAME = "platform-runtime-failover"
DEFAULT_TTL_SECONDS = 300
DEFAULT_TOKEN_PATH = ".build/failover-lock-token.json"


class LockError(RuntimeError):
    """Base class for failover lock errors."""


class LockAlreadyHeldError(LockError):
    """Raised when acquire fails because another holder already owns the lock."""


class LockOwnershipError(LockError):
    """Raised when release fails due to lock ownership mismatch."""


@dataclass(frozen=True)
class LockRecord:
    lock_name: str
    lock_id: str
    acquired_by: str
    acquired_at: str
    ttl: int

    @property
    def pk(self) -> str:
        return f"LOCK#{self.lock_name}"

    @property
    def sk(self) -> str:
        return "METADATA"


@dataclass(frozen=True)
class LocalToken:
    lock_id: str
    table_name: str
    lock_name: str


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso8601_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def get_aws_region() -> str:
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION environment variable not set")
    return region


def resolve_table_name() -> str:
    return os.environ.get("PLATFORM_OPS_LOCKS_TABLE", DEFAULT_TABLE_NAME)


def resolve_token_path() -> Path:
    return Path(os.environ.get("FAILOVER_LOCK_TOKEN_PATH", DEFAULT_TOKEN_PATH))


def default_owner() -> str:
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    host = socket.gethostname() or "unknown-host"
    return f"ops/failover_lock.py:{user}@{host}"


def _is_conditional_check_failed(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def acquire_lock(
    ddb_client: Any,
    *,
    table_name: str,
    lock_name: str = DEFAULT_LOCK_NAME,
    acquired_by: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> LockRecord:
    current_time = now or now_utc()
    lock_record = LockRecord(
        lock_name=lock_name,
        lock_id=str(uuid4()),
        acquired_by=acquired_by,
        acquired_at=iso8601_utc(current_time),
        ttl=int(current_time.timestamp()) + ttl_seconds,
    )
    item = {
        "PK": {"S": lock_record.pk},
        "SK": {"S": lock_record.sk},
        "lockName": {"S": lock_record.lock_name},
        "lockId": {"S": lock_record.lock_id},
        "acquiredBy": {"S": lock_record.acquired_by},
        "acquiredAt": {"S": lock_record.acquired_at},
        "ttl": {"N": str(lock_record.ttl)},
    }
    try:
        ddb_client.put_item(
            TableName=table_name,
            Item=item,
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ClientError as exc:
        if _is_conditional_check_failed(exc):
            raise LockAlreadyHeldError(f"Lock already held: {lock_name}") from exc
        raise
    return lock_record


def release_lock(
    ddb_client: Any,
    *,
    table_name: str,
    lock_name: str = DEFAULT_LOCK_NAME,
    lock_id: str | None = None,
) -> bool:
    delete_kwargs: dict[str, Any] = {
        "TableName": table_name,
        "Key": {"PK": {"S": f"LOCK#{lock_name}"}, "SK": {"S": "METADATA"}},
        "ReturnValues": "ALL_OLD",
    }
    if lock_id:
        delete_kwargs["ConditionExpression"] = "lockId = :lock_id"
        delete_kwargs["ExpressionAttributeValues"] = {":lock_id": {"S": lock_id}}
    try:
        response = ddb_client.delete_item(**delete_kwargs)
    except ClientError as exc:
        if _is_conditional_check_failed(exc):
            raise LockOwnershipError(
                f"Lock ownership mismatch for {lock_name}; refusing to release"
            ) from exc
        raise
    return "Attributes" in response


def save_local_token(token: LocalToken) -> None:
    token_path = resolve_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(
        json.dumps(
            {
                "lockId": token.lock_id,
                "tableName": token.table_name,
                "lockName": token.lock_name,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load_local_token() -> LocalToken | None:
    token_path = resolve_token_path()
    if not token_path.exists():
        return None
    data = json.loads(token_path.read_text(encoding="utf-8"))
    lock_id = str(data.get("lockId", "")).strip()
    table_name = str(data.get("tableName", "")).strip()
    lock_name = str(data.get("lockName", "")).strip()
    if not (lock_id and table_name and lock_name):
        return None
    return LocalToken(lock_id=lock_id, table_name=table_name, lock_name=lock_name)


def clear_local_token() -> None:
    token_path = resolve_token_path()
    if token_path.exists():
        token_path.unlink()


@contextmanager
def held_lock(
    ddb_client: Any,
    *,
    table_name: str,
    lock_name: str = DEFAULT_LOCK_NAME,
    acquired_by: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Iterator[LockRecord]:
    record = acquire_lock(
        ddb_client,
        table_name=table_name,
        lock_name=lock_name,
        acquired_by=acquired_by,
        ttl_seconds=ttl_seconds,
    )
    try:
        yield record
    finally:
        try:
            release_lock(
                ddb_client,
                table_name=table_name,
                lock_name=lock_name,
                lock_id=record.lock_id,
            )
        except LockOwnershipError:
            # Lock already rotated/expired; nothing else to do on best-effort cleanup.
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    acquire = subparsers.add_parser("acquire", help="Acquire distributed runtime failover lock")
    acquire.add_argument("--env", default="dev", help="Environment label for operator context")
    acquire.add_argument("--owner", default=default_owner(), help="Lock owner identity")
    acquire.add_argument(
        "--ttl-seconds",
        type=int,
        default=DEFAULT_TTL_SECONDS,
        help="Lock TTL in seconds (default 300)",
    )
    acquire.add_argument(
        "--table-name",
        default=resolve_table_name(),
        help="DynamoDB table name (default platform-ops-locks)",
    )

    release = subparsers.add_parser("release", help="Release distributed runtime failover lock")
    release.add_argument("--env", default="dev", help="Environment label for operator context")
    release.add_argument(
        "--lock-id",
        default=None,
        help="Expected lockId to release (optional; uses saved local token by default)",
    )
    release.add_argument(
        "--table-name",
        default=resolve_table_name(),
        help="DynamoDB table name (default platform-ops-locks)",
    )
    release.add_argument(
        "--force",
        action="store_true",
        help="Release without validating lockId ownership",
    )

    return parser.parse_args(argv)


def cmd_acquire(args: argparse.Namespace) -> int:
    region = get_aws_region()
    ddb_client = boto3.client("dynamodb", region_name=region)
    try:
        record = acquire_lock(
            ddb_client,
            table_name=args.table_name,
            lock_name=DEFAULT_LOCK_NAME,
            acquired_by=args.owner,
            ttl_seconds=args.ttl_seconds,
        )
    except LockAlreadyHeldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    save_local_token(
        LocalToken(lock_id=record.lock_id, table_name=args.table_name, lock_name=record.lock_name)
    )
    print(f"Lock acquired: {record.lock_name}")
    print(f"lock_id={record.lock_id}")
    print(f"expires_at={record.ttl}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    region = get_aws_region()
    ddb_client = boto3.client("dynamodb", region_name=region)

    lock_id = args.lock_id
    token = load_local_token()
    if lock_id is None and token is not None:
        lock_id = token.lock_id
    if not lock_id and not args.force:
        print(
            "No lock token found. Provide --lock-id or use --force for unconditional release.",
            file=sys.stderr,
        )
        return 2

    try:
        released = release_lock(
            ddb_client,
            table_name=args.table_name,
            lock_name=DEFAULT_LOCK_NAME,
            lock_id=None if args.force else lock_id,
        )
    except LockOwnershipError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if released:
        print(f"Lock released: {DEFAULT_LOCK_NAME}")
    else:
        print(f"Lock not present: {DEFAULT_LOCK_NAME}")
    clear_local_token()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "acquire":
        return cmd_acquire(args)
    if args.command == "release":
        return cmd_release(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
