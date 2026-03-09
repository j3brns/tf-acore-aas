#!/usr/bin/env python3
"""Backfill/verify tenant execution role ARNs from authoritative SSM parameters."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

ROLE_ARN_PATTERN = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):iam::(?P<account_id>\d{12}):role/(?P<role_name>[\w+=,.@\-_/]+)$"
)
DEFAULT_TABLE_NAME = "platform-tenants"
DEFAULT_PARAM_TEMPLATE = "/platform/tenants/{tenant_id}/execution-role-arn"


@dataclass(frozen=True)
class TenantRow:
    pk: str
    sk: str
    tenant_id: str
    account_id: str
    execution_role_arn: str | None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_role_arn(role_arn: str, account_id: str) -> str | None:
    match = ROLE_ARN_PATTERN.fullmatch(role_arn)
    if not match:
        return "malformed"
    if match.group("account_id") != account_id:
        return "account-mismatch"
    return None


def _read_execution_role_from_ssm(ssm: Any, *, tenant_id: str, param_template: str) -> str | None:
    parameter_name = param_template.format(tenant_id=tenant_id)
    try:
        response = ssm.get_parameter(Name=parameter_name)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code == "ParameterNotFound":
            return None
        raise
    parameter = response.get("Parameter", {})
    return _str_or_none(parameter.get("Value"))


def _tenant_row(item: dict[str, Any]) -> TenantRow | None:
    pk = _str_or_none(item.get("PK"))
    sk = _str_or_none(item.get("SK"))
    tenant_id = _str_or_none(item.get("tenant_id") or item.get("tenantId"))
    account_id = _str_or_none(item.get("account_id") or item.get("accountId"))
    execution_role_arn = _str_or_none(
        item.get("execution_role_arn") or item.get("executionRoleArn")
    )
    if not pk or not sk or not tenant_id or not account_id:
        return None
    return TenantRow(
        pk=pk,
        sk=sk,
        tenant_id=tenant_id,
        account_id=account_id,
        execution_role_arn=execution_role_arn,
    )


def _scan_tenant_rows(table: Any, *, tenant_id: str | None) -> list[TenantRow]:
    if tenant_id:
        item = table.get_item(Key={"PK": f"TENANT#{tenant_id}", "SK": "METADATA"}).get("Item")
        row = _tenant_row(item) if isinstance(item, dict) else None
        return [row] if row else []

    rows: list[TenantRow] = []
    scan_kwargs: dict[str, Any] = {"FilterExpression": Attr("SK").eq("METADATA")}
    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            if not isinstance(item, dict):
                continue
            row = _tenant_row(item)
            if row is not None:
                rows.append(row)
        if "LastEvaluatedKey" not in response:
            break
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return rows


def _apply_backfill(table: Any, *, row: TenantRow, execution_role_arn: str) -> None:
    table.update_item(
        Key={"PK": row.pk, "SK": row.sk},
        UpdateExpression=(
            "SET executionRoleArn = :executionRoleArn, "
            "execution_role_arn = :executionRoleArn, "
            "updatedAt = :updatedAt"
        ),
        ExpressionAttributeValues={
            ":executionRoleArn": execution_role_arn,
            ":updatedAt": _utc_now(),
        },
        ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
    )


def run(args: argparse.Namespace) -> int:
    if not args.region:
        print("AWS region is required. Set AWS_REGION or pass --region.")
        return 2

    session = boto3.session.Session(region_name=args.region)
    ddb = session.resource("dynamodb")
    ssm = session.client("ssm")
    table = ddb.Table(args.table_name)

    rows = _scan_tenant_rows(table, tenant_id=args.tenant_id)
    if not rows:
        print("No tenant rows found")
        return 1

    verified = 0
    backfilled = 0
    unresolved = 0
    malformed = 0

    for row in rows:
        current = row.execution_role_arn
        if current:
            validation_error = _validate_role_arn(current, row.account_id)
            if validation_error is None:
                verified += 1
                print(f"[verified] tenant={row.tenant_id} source=record roleArn={current}")
                continue
            malformed += 1
            print(
                "[invalid] "
                f"tenant={row.tenant_id} "
                "source=record "
                f"error={validation_error} "
                f"roleArn={current}"
            )
            continue

        resolved = _read_execution_role_from_ssm(
            ssm,
            tenant_id=row.tenant_id,
            param_template=args.param_template,
        )
        if not resolved:
            unresolved += 1
            print(f"[missing] tenant={row.tenant_id} source=ssm")
            continue

        validation_error = _validate_role_arn(resolved, row.account_id)
        if validation_error is not None:
            malformed += 1
            print(
                "[invalid] "
                f"tenant={row.tenant_id} "
                "source=ssm "
                f"error={validation_error} "
                f"roleArn={resolved}"
            )
            continue

        verified += 1
        if args.apply:
            _apply_backfill(table, row=row, execution_role_arn=resolved)
            backfilled += 1
            print(f"[backfilled] tenant={row.tenant_id} roleArn={resolved}")
        else:
            print(f"[ready] tenant={row.tenant_id} roleArn={resolved}")

    print(
        "Summary:",
        f"scanned={len(rows)}",
        f"verified={verified}",
        f"backfilled={backfilled}",
        f"unresolved={unresolved}",
        f"invalid={malformed}",
    )
    if unresolved > 0 or malformed > 0:
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill and verify executionRoleArn for tenant records."
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION"), help="AWS region")
    parser.add_argument("--table-name", default=DEFAULT_TABLE_NAME, help="Tenant table name")
    parser.add_argument(
        "--param-template",
        default=DEFAULT_PARAM_TEMPLATE,
        help="SSM parameter template (must include {tenant_id})",
    )
    parser.add_argument("--tenant-id", default=None, help="Only process one tenant ID")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write back resolved ARN to tenant records. Default is verify-only mode.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
