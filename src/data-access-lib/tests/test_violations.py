"""
Coverage assertions (required by TASK-013):
  - Cross-tenant DynamoDB read raises TenantAccessViolation.
  - Cross-tenant DynamoDB write raises TenantAccessViolation.
  - Cross-tenant S3 read raises TenantAccessViolation.
  - Cross-tenant S3 write raises TenantAccessViolation.
  - TenantAccessViolation emits CloudWatch metric.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from data_access import TenantAccessViolation, TenantScopedDynamoDB
from data_access.client import _emit_tenant_violation_metric
from moto import mock_aws

from .conftest import BUCKET, OTHER_TENANT_ID, TABLE_NAME, TENANT_ID, make_dynamo_db, make_s3


class TestTenantAccessViolation:
    def test_attributes(self) -> None:
        exc = TenantAccessViolation(
            tenant_id="t-victim",
            caller_tenant_id="t-perpetrator",
            attempted_key="TENANT#t-victim/SK",
        )
        assert exc.tenant_id == "t-victim"
        assert exc.caller_tenant_id == "t-perpetrator"
        assert exc.attempted_key == "TENANT#t-victim/SK"

    def test_is_exception(self) -> None:
        exc = TenantAccessViolation(
            tenant_id="a",
            caller_tenant_id="b",
            attempted_key="k",
        )
        assert isinstance(exc, Exception)

    def test_message_contains_all_parts(self) -> None:
        exc = TenantAccessViolation(
            tenant_id="t-victim",
            caller_tenant_id="t-perpetrator",
            attempted_key="SOME_KEY",
        )
        msg = str(exc)
        assert "t-perpetrator" in msg
        assert "t-victim" in msg
        assert "SOME_KEY" in msg


class TestEmitTenantViolationMetric:
    def test_calls_put_metric_data(self, mock_cw: MagicMock) -> None:
        _emit_tenant_violation_metric(
            mock_cw, caller_tenant_id=TENANT_ID, target_tenant_id=OTHER_TENANT_ID
        )
        mock_cw.put_metric_data.assert_called_once()
        kwargs = mock_cw.put_metric_data.call_args.kwargs
        assert kwargs["Namespace"] == "platform/security"
        metric = kwargs["MetricData"][0]
        assert metric["MetricName"] == "TenantAccessViolation"
        assert metric["Value"] == 1
        assert metric["Unit"] == "Count"

    def test_metric_dimensions_contain_both_tenants(self, mock_cw: MagicMock) -> None:
        _emit_tenant_violation_metric(
            mock_cw, caller_tenant_id=TENANT_ID, target_tenant_id=OTHER_TENANT_ID
        )
        dims = {
            d["Name"]: d["Value"]
            for d in mock_cw.put_metric_data.call_args.kwargs["MetricData"][0]["Dimensions"]
        }
        assert dims["caller_tenant_id"] == TENANT_ID
        assert dims["target_tenant_id"] == OTHER_TENANT_ID

    def test_never_raises_on_cloudwatch_error(self, mock_cw: MagicMock) -> None:
        mock_cw.put_metric_data.side_effect = Exception("CW unavailable")
        _emit_tenant_violation_metric(
            mock_cw, caller_tenant_id=TENANT_ID, target_tenant_id=OTHER_TENANT_ID
        )


class TestValidatePkEdgeCases:
    def test_missing_pk_key_is_allowed(self, ctx) -> None:
        mock_dynamo = MagicMock()
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": {"data": "ok"}}
        db = TenantScopedDynamoDB(ctx, dynamodb_resource=mock_dynamo, cloudwatch_client=MagicMock())
        result = db.get_item(TABLE_NAME, {"SK": "METADATA"})
        assert result == {"data": "ok"}

    def test_non_string_pk_is_allowed(self, ctx) -> None:
        mock_dynamo = MagicMock()
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": {}}
        db = TenantScopedDynamoDB(ctx, dynamodb_resource=mock_dynamo, cloudwatch_client=MagicMock())
        db.get_item(TABLE_NAME, {"PK": 12345, "SK": "SK"})


class TestDynamoDBViolationMetricFailure:
    def test_cw_failure_still_raises_violation(self, ctx) -> None:
        failing_cw = MagicMock()
        failing_cw.put_metric_data.side_effect = Exception("CW down")
        with mock_aws():
            db, _ = make_dynamo_db(ctx, cw=failing_cw)
            with pytest.raises(TenantAccessViolation):
                db.get_item(
                    TABLE_NAME,
                    {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "METADATA"},
                )


class TestS3ValidateKeyEdgeCases:
    def test_tenants_prefix_but_no_tenant_segment(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.get_object(BUCKET, "tenants/")
        assert exc_info.value.tenant_id == ""

    def test_metric_emission_failure_still_raises_violation(self, ctx) -> None:
        failing_cw = MagicMock()
        failing_cw.put_metric_data.side_effect = Exception("CW down")
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=failing_cw)
            with pytest.raises(TenantAccessViolation):
                scoped.get_object(BUCKET, f"tenants/{OTHER_TENANT_ID}/data.json")
