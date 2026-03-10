from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import backfill_tenant_execution_role_arn as script


class FakeTable:
    def __init__(self, *items: dict[str, str]):
        self.items = {(str(item["PK"]), str(item["SK"])): dict(item) for item in items}

    def get_item(self, *, Key: dict[str, str]) -> dict[str, dict[str, str]]:
        item = self.items.get((Key["PK"], Key["SK"]))
        if item is None:
            return {}
        return {"Item": dict(item)}

    def scan(self, **_: object) -> dict[str, object]:
        return {"Items": [dict(item) for item in self.items.values()], "LastEvaluatedKey": None}

    def update_item(
        self,
        *,
        Key: dict[str, str],
        UpdateExpression: str,
        ExpressionAttributeValues: dict[str, str],
        ConditionExpression: str,
    ) -> None:
        assert "executionRoleArn" in UpdateExpression
        assert ConditionExpression == "attribute_exists(PK) AND attribute_exists(SK)"
        item = self.items[(Key["PK"], Key["SK"])]
        item["executionRoleArn"] = ExpressionAttributeValues[":executionRoleArn"]
        item["execution_role_arn"] = ExpressionAttributeValues[":executionRoleArn"]
        item["updatedAt"] = ExpressionAttributeValues[":updatedAt"]


class FakeDynamoResource:
    def __init__(self, table: FakeTable):
        self._table = table

    def Table(self, table_name: str) -> FakeTable:
        assert table_name == "platform-tenants"
        return self._table


class FakeSsmClient:
    def __init__(self, parameters: dict[str, str | None]):
        self.parameters = dict(parameters)
        self.requests: list[str] = []

    def get_parameter(self, *, Name: str) -> dict[str, dict[str, str | None]]:
        self.requests.append(Name)
        if Name not in self.parameters:
            raise ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")
        return {"Parameter": {"Value": self.parameters[Name]}}


class FakeSession:
    def __init__(self, *, table: FakeTable, ssm: FakeSsmClient):
        self._ddb = FakeDynamoResource(table)
        self._ssm = ssm

    def resource(self, service_name: str) -> FakeDynamoResource:
        assert service_name == "dynamodb"
        return self._ddb

    def client(self, service_name: str) -> FakeSsmClient:
        assert service_name == "ssm"
        return self._ssm


def _run(
    *,
    table: FakeTable,
    parameters: dict[str, str | None],
    apply: bool,
    tenant_id: str | None = None,
) -> tuple[int, FakeSsmClient]:
    fake_ssm = FakeSsmClient(parameters)
    fake_session = FakeSession(table=table, ssm=fake_ssm)
    args = argparse.Namespace(
        region="eu-west-2",
        table_name="platform-tenants",
        param_template="/platform/tenants/{tenant_id}/execution-role-arn",
        tenant_id=tenant_id,
        apply=apply,
    )
    with patch.object(script.boto3.session, "Session", return_value=fake_session):
        rc = script.run(args)
    return rc, fake_ssm


@pytest.mark.parametrize(
    ("invalid_record_arn", "expected_error"),
    [
        ("not-an-arn", "malformed"),
        ("arn:aws:iam::999999999999:role/wrong-account-role", "account-mismatch"),
    ],
)
def test_backfill_apply_repairs_invalid_record_from_ssm(
    capsys: pytest.CaptureFixture[str],
    invalid_record_arn: str,
    expected_error: str,
) -> None:
    table = FakeTable(
        {
            "PK": "TENANT#t-001",
            "SK": "METADATA",
            "tenantId": "t-001",
            "accountId": "123456789012",
            "executionRoleArn": invalid_record_arn,
        }
    )

    rc, fake_ssm = _run(
        table=table,
        parameters={
            "/platform/tenants/t-001/execution-role-arn": (
                "arn:aws:iam::123456789012:role/tenant-custom-role"
            )
        },
        apply=True,
    )

    assert rc == 0
    item = table.get_item(Key={"PK": "TENANT#t-001", "SK": "METADATA"})["Item"]
    assert item["executionRoleArn"] == "arn:aws:iam::123456789012:role/tenant-custom-role"
    assert item["execution_role_arn"] == "arn:aws:iam::123456789012:role/tenant-custom-role"
    assert fake_ssm.requests == ["/platform/tenants/t-001/execution-role-arn"]
    output = capsys.readouterr().out
    assert f"[invalid-record] tenant=t-001 source=record error={expected_error}" in output
    assert (
        "[backfilled] tenant=t-001 source=ssm reason=record-invalid "
        "roleArn=arn:aws:iam::123456789012:role/tenant-custom-role"
    ) in output
    assert "already_valid=0" in output
    assert "repaired=1" in output
    assert "invalid_record=1" in output
    assert "invalid_ssm=0" in output


def test_backfill_fails_closed_when_record_invalid_and_ssm_invalid(
    capsys: pytest.CaptureFixture[str],
) -> None:
    table = FakeTable(
        {
            "PK": "TENANT#t-002",
            "SK": "METADATA",
            "tenantId": "t-002",
            "accountId": "123456789012",
            "executionRoleArn": "arn:aws:iam::999999999999:role/wrong-account-role",
        }
    )

    rc, fake_ssm = _run(
        table=table,
        parameters={"/platform/tenants/t-002/execution-role-arn": "not-an-arn"},
        apply=False,
    )

    assert rc == 1
    assert fake_ssm.requests == ["/platform/tenants/t-002/execution-role-arn"]
    output = capsys.readouterr().out
    assert "[invalid-record] tenant=t-002 source=record error=account-mismatch" in output
    assert (
        "[invalid-ssm] tenant=t-002 source=ssm reason=record-invalid error=malformed "
        "roleArn=not-an-arn"
    ) in output
    assert "invalid_record=1" in output
    assert "invalid_ssm=1" in output
    assert "repaired=0" in output


def test_backfill_valid_record_skips_ssm_lookup(capsys: pytest.CaptureFixture[str]) -> None:
    table = FakeTable(
        {
            "PK": "TENANT#t-003",
            "SK": "METADATA",
            "tenantId": "t-003",
            "accountId": "123456789012",
            "executionRoleArn": "arn:aws:iam::123456789012:role/already-valid",
        }
    )

    rc, fake_ssm = _run(table=table, parameters={}, apply=False)

    assert rc == 0
    assert fake_ssm.requests == []
    output = capsys.readouterr().out
    assert (
        "[verified] tenant=t-003 source=record roleArn=arn:aws:iam::123456789012:role/already-valid"
        in output
    )
    assert "already_valid=1" in output
    assert "repaired=0" in output
    assert "ready_from_ssm=0" in output


def test_scan_stops_when_last_evaluated_key_is_none() -> None:
    table = FakeTable(
        {
            "PK": "TENANT#t-004",
            "SK": "METADATA",
            "tenantId": "t-004",
            "accountId": "123456789012",
        }
    )

    rows = script._scan_tenant_rows(table, tenant_id=None)

    assert [row.tenant_id for row in rows] == ["t-004"]
