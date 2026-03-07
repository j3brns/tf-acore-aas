"""Unit tests for scripts/failover_lock.py (TASK-030)."""

from __future__ import annotations

import importlib.util
import io
import sys
import threading
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws


def _load_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "failover_lock_script", repo_root / "scripts" / "failover_lock.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


failover_lock = _load_module()
_REGION = "eu-west-2"
_TABLE_NAME = "platform-ops-locks"


def _create_ops_lock_table() -> None:
    ddb = boto3.client("dynamodb", region_name=_REGION)
    ddb.create_table(
        TableName=_TABLE_NAME,
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


def test_parse_args_for_acquire_and_release() -> None:
    acquire = failover_lock.parse_args(["acquire", "--env", "prod"])
    release = failover_lock.parse_args(["release", "--env", "staging", "--force"])
    assert acquire.command == "acquire"
    assert acquire.env == "prod"
    assert release.command == "release"
    assert release.env == "staging"
    assert release.force is True


@mock_aws
def test_acquire_lock_writes_expected_record() -> None:
    _create_ops_lock_table()
    client = boto3.client("dynamodb", region_name=_REGION)
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    record = failover_lock.acquire_lock(
        client,
        table_name=_TABLE_NAME,
        acquired_by="ops@test",
        now=now,
        ttl_seconds=300,
    )

    item = client.get_item(
        TableName=_TABLE_NAME,
        Key={"PK": {"S": "LOCK#platform-runtime-failover"}, "SK": {"S": "METADATA"}},
        ConsistentRead=True,
    )["Item"]
    assert item["lockId"]["S"] == record.lock_id
    assert item["acquiredBy"]["S"] == "ops@test"
    assert item["acquiredAt"]["S"] == "2026-01-01T12:00:00Z"
    assert int(item["ttl"]["N"]) == int(now.timestamp()) + 300


@mock_aws
def test_concurrent_acquire_only_one_succeeds() -> None:
    _create_ops_lock_table()
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def _contender(identity: str) -> None:
        client = boto3.client("dynamodb", region_name=_REGION)
        barrier.wait()
        try:
            failover_lock.acquire_lock(
                client,
                table_name=_TABLE_NAME,
                acquired_by=identity,
            )
            outcome = "success"
        except failover_lock.LockAlreadyHeldError:
            outcome = "held"
        with lock:
            results.append(outcome)

    t1 = threading.Thread(target=_contender, args=("ops/a",))
    t2 = threading.Thread(target=_contender, args=("ops/b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results.count("success") == 1
    assert results.count("held") == 1


@mock_aws
def test_release_requires_matching_lock_id_when_provided() -> None:
    _create_ops_lock_table()
    client = boto3.client("dynamodb", region_name=_REGION)
    record = failover_lock.acquire_lock(client, table_name=_TABLE_NAME, acquired_by="ops@test")

    with pytest.raises(failover_lock.LockOwnershipError):
        failover_lock.release_lock(
            client,
            table_name=_TABLE_NAME,
            lock_id="not-the-right-lock-id",
        )

    still_present = client.get_item(
        TableName=_TABLE_NAME,
        Key={"PK": {"S": "LOCK#platform-runtime-failover"}, "SK": {"S": "METADATA"}},
        ConsistentRead=True,
    )
    assert "Item" in still_present

    released = failover_lock.release_lock(
        client,
        table_name=_TABLE_NAME,
        lock_id=record.lock_id,
    )
    assert released is True
    gone = client.get_item(
        TableName=_TABLE_NAME,
        Key={"PK": {"S": "LOCK#platform-runtime-failover"}, "SK": {"S": "METADATA"}},
        ConsistentRead=True,
    )
    assert "Item" not in gone


@mock_aws
def test_cli_acquire_then_release_uses_local_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create_ops_lock_table()
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("FAILOVER_LOCK_TOKEN_PATH", str(tmp_path / "lock-token.json"))

    stdout_buffer = io.StringIO()
    with redirect_stdout(stdout_buffer):
        acquire_rc = failover_lock.main(["acquire", "--env", "prod"])
    assert acquire_rc == 0
    assert "Lock acquired: platform-runtime-failover" in stdout_buffer.getvalue()

    stdout_buffer = io.StringIO()
    with redirect_stdout(stdout_buffer):
        release_rc = failover_lock.main(["release", "--env", "prod"])
    assert release_rc == 0
    assert "Lock released: platform-runtime-failover" in stdout_buffer.getvalue()
