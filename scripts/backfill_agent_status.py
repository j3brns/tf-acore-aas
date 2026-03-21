#!/usr/bin/env python3
"""Backfill agent status to RELEASED for existing records (ADR-015)."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

DEFAULT_TABLE_NAME = "platform-agents"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _scan_agent_rows(table: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scan_kwargs: dict[str, Any] = {}
    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            if not isinstance(item, dict):
                continue
            rows.append(item)
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_evaluated_key
    return rows


def _apply_backfill(table: Any, *, item: dict[str, Any]) -> None:
    table.update_item(
        Key={"PK": item["PK"], "SK": item["SK"]},
        UpdateExpression="SET #s = :s, #ua = :ua",
        ExpressionAttributeNames={"#s": "status", "#ua": "updated_at"},
        ExpressionAttributeValues={
            ":s": "released",
            ":ua": _utc_now(),
        },
        ConditionExpression=(
            "attribute_exists(PK) AND attribute_exists(SK) AND attribute_not_exists(#s)"
        ),
    )


def run(args: argparse.Namespace) -> int:
    region = args.region or os.environ.get("AWS_REGION")
    if not region:
        print("AWS region is required. Set AWS_REGION or pass --region.")
        return 2

    session = boto3.session.Session(region_name=region)
    ddb = session.resource("dynamodb")
    table = ddb.Table(args.table_name)

    rows = _scan_agent_rows(table)
    if not rows:
        print("No agent rows found")
        return 0

    to_backfill = [r for r in rows if "status" not in r]
    print(f"Found {len(rows)} agent records, {len(to_backfill)} need backfill.")

    if not to_backfill:
        return 0

    backfilled = 0
    for row in to_backfill:
        agent_name = row.get("agent_name", "unknown")
        version = row.get("version", "unknown")
        if args.apply:
            try:
                _apply_backfill(table, item=row)
                backfilled += 1
                print(f"[backfilled] agent={agent_name} version={version}")
            except ClientError as e:
                print(f"[failed] agent={agent_name} version={version} error={e}")
        else:
            print(f"[ready] agent={agent_name} version={version}")

    print(f"Summary: scanned={len(rows)} to_backfill={len(to_backfill)} backfilled={backfilled}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill agent status.")
    parser.add_argument("--region", help="AWS region")
    parser.add_argument("--table-name", default=DEFAULT_TABLE_NAME, help="Agent table name")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write back status=released to agent records. Default is verify-only mode.",
    )
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
