"""
tests/test_client.py — Security-critical tests for TenantScopedDynamoDB and TenantScopedS3.

Coverage assertions (required by TASK-013):
  - Cross-tenant DynamoDB read raises TenantAccessViolation.
  - Cross-tenant DynamoDB write raises TenantAccessViolation.
  - Cross-tenant S3 read raises TenantAccessViolation.
  - Cross-tenant S3 write raises TenantAccessViolation.
  - TenantAccessViolation emits CloudWatch metric.

100% coverage required. No security-critical branch may be left untested.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import boto3
import pytest
from data_access import TenantAccessViolation, TenantContext, TenantScopedDynamoDB, TenantScopedS3
from data_access.client import _emit_tenant_violation_metric
from data_access.models import TenantTier
from moto import mock_aws

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TENANT_ID = "t-abc123"
OTHER_TENANT_ID = "t-xyz789"
APP_ID = "app-001"
REGION = "eu-west-2"
TABLE_NAME = "platform-invocations"
BUCKET = "platform-results"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set minimal AWS env vars required by the library and moto."""
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


@pytest.fixture
def ctx() -> TenantContext:
    return TenantContext(
        tenant_id=TENANT_ID,
        app_id=APP_ID,
        tier=TenantTier.STANDARD,
        sub="user-001",
    )


@pytest.fixture
def mock_cw() -> MagicMock:
    """A MagicMock standing in for a boto3 CloudWatch client."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Helper: build a DynamoDB client with mocked resources
# ---------------------------------------------------------------------------


def _make_dynamo_db(ctx: TenantContext, *, cw: Any = None) -> tuple[TenantScopedDynamoDB, Any]:
    """Return (TenantScopedDynamoDB, moto_dynamodb_resource) inside mock_aws context.

    Caller must enter mock_aws() before calling this.
    """
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    dynamodb.create_table(
        TableName=TABLE_NAME,
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
    cw_client = cw or MagicMock()
    db = TenantScopedDynamoDB(ctx, dynamodb_resource=dynamodb, cloudwatch_client=cw_client)
    return db, dynamodb


def _make_s3(ctx: TenantContext, *, cw: Any = None) -> tuple[TenantScopedS3, Any]:
    """Return (TenantScopedS3, moto_s3_client) inside mock_aws context."""
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket=BUCKET,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    cw_client = cw or MagicMock()
    scoped = TenantScopedS3(ctx, s3_client=s3, cloudwatch_client=cw_client)
    return scoped, s3


# ===========================================================================
# TenantAccessViolation exception
# ===========================================================================


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


# ===========================================================================
# _emit_tenant_violation_metric (module-level helper)
# ===========================================================================


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
        """Metric emission failure must not propagate — violation must still raise."""
        mock_cw.put_metric_data.side_effect = Exception("CW unavailable")
        # Should not raise
        _emit_tenant_violation_metric(
            mock_cw, caller_tenant_id=TENANT_ID, target_tenant_id=OTHER_TENANT_ID
        )


# ===========================================================================
# TenantScopedDynamoDB — init
# ===========================================================================


class TestTenantScopedDynamoDBInit:
    def test_init_with_injected_clients(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        mock_dynamo = MagicMock()
        db = TenantScopedDynamoDB(ctx, dynamodb_resource=mock_dynamo, cloudwatch_client=mock_cw)
        assert db._tenant_id == TENANT_ID
        assert db._app_id == APP_ID

    def test_init_without_injected_clients_uses_env_region(self, ctx: TenantContext) -> None:
        """Verify the default path creates boto3 clients using AWS_REGION."""
        with mock_aws():
            db = TenantScopedDynamoDB(ctx)
        assert db._tenant_id == TENANT_ID


# ===========================================================================
# TenantScopedDynamoDB — get_item
# ===========================================================================


class TestTenantScopedDynamoDBGetItem:
    def test_get_item_own_tenant_found(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            # Seed item
            dynamo.Table(TABLE_NAME).put_item(
                Item={"PK": f"TENANT#{TENANT_ID}", "SK": "METADATA", "data": "hello"}
            )
            item = db.get_item(TABLE_NAME, {"PK": f"TENANT#{TENANT_ID}", "SK": "METADATA"})
        assert item is not None
        assert item["data"] == "hello"

    def test_get_item_own_tenant_not_found_returns_none(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            db, _ = _make_dynamo_db(ctx, cw=mock_cw)
            result = db.get_item(TABLE_NAME, {"PK": f"TENANT#{TENANT_ID}", "SK": "MISSING"})
        assert result is None

    def test_get_item_non_tenant_pk_allowed(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        """Non-TENANT# prefix keys (e.g. AGENT#) are not partition-checked."""
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            dynamo.Table(TABLE_NAME).put_item(
                Item={"PK": "AGENT#echo-agent", "SK": "VERSION#1.0.0"}
            )
            item = db.get_item(TABLE_NAME, {"PK": "AGENT#echo-agent", "SK": "VERSION#1.0.0"})
        assert item is not None
        mock_cw.put_metric_data.assert_not_called()

    # -------------------------------------------------------------------
    # REQUIRED coverage assertion: cross-tenant read raises violation
    # -------------------------------------------------------------------
    def test_get_item_cross_tenant_raises_violation(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            db, _ = _make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                db.get_item(
                    TABLE_NAME,
                    {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "METADATA"},
                )
        exc = exc_info.value
        assert exc.caller_tenant_id == TENANT_ID
        assert exc.tenant_id == OTHER_TENANT_ID

    def test_get_item_cross_tenant_emits_cloudwatch_metric(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        """REQUIRED: TenantAccessViolation emits CloudWatch metric."""
        with mock_aws():
            db, _ = _make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation):
                db.get_item(
                    TABLE_NAME,
                    {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "METADATA"},
                )
        mock_cw.put_metric_data.assert_called_once()
        kwargs = mock_cw.put_metric_data.call_args.kwargs
        assert kwargs["Namespace"] == "platform/security"
        assert kwargs["MetricData"][0]["MetricName"] == "TenantAccessViolation"


# ===========================================================================
# TenantScopedDynamoDB — put_item
# ===========================================================================


class TestTenantScopedDynamoDBPutItem:
    def test_put_item_own_tenant_succeeds(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            db.put_item(
                TABLE_NAME,
                {"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001", "tokens": 100},
            )
            item = dynamo.Table(TABLE_NAME).get_item(
                Key={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001"}
            )
        assert item["Item"]["tokens"] == 100
        mock_cw.put_metric_data.assert_not_called()

    # -------------------------------------------------------------------
    # REQUIRED coverage assertion: cross-tenant write raises violation
    # -------------------------------------------------------------------
    def test_put_item_cross_tenant_raises_violation(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            db, _ = _make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                db.put_item(
                    TABLE_NAME,
                    {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "INV#001"},
                )
        exc = exc_info.value
        assert exc.caller_tenant_id == TENANT_ID
        assert exc.tenant_id == OTHER_TENANT_ID

    def test_put_item_cross_tenant_emits_cloudwatch_metric(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        """REQUIRED: TenantAccessViolation (write path) emits CloudWatch metric."""
        with mock_aws():
            db, _ = _make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation):
                db.put_item(
                    TABLE_NAME,
                    {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "INV#001"},
                )
        mock_cw.put_metric_data.assert_called_once()

    def test_put_item_non_tenant_pk_allowed(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = _make_dynamo_db(ctx, cw=mock_cw)
            db.put_item(TABLE_NAME, {"PK": "LOCK#failover", "SK": "METADATA"})
        mock_cw.put_metric_data.assert_not_called()


# ===========================================================================
# TenantScopedDynamoDB — update_item
# ===========================================================================


class TestTenantScopedDynamoDBUpdateItem:
    def test_update_item_own_tenant_succeeds(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            dynamo.Table(TABLE_NAME).put_item(
                Item={"PK": f"TENANT#{TENANT_ID}", "SK": "SESS#001", "status": "active"}
            )
            result = db.update_item(
                TABLE_NAME,
                key={"PK": f"TENANT#{TENANT_ID}", "SK": "SESS#001"},
                update_expression="SET #s = :s",
                expression_attribute_values={":s": "completed"},
                expression_attribute_names={"#s": "status"},
            )
        assert result["Attributes"]["status"] == "completed"

    def test_update_item_with_condition_expression(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            dynamo.Table(TABLE_NAME).put_item(
                Item={"PK": f"TENANT#{TENANT_ID}", "SK": "SESS#002", "status": "active"}
            )
            result = db.update_item(
                TABLE_NAME,
                key={"PK": f"TENANT#{TENANT_ID}", "SK": "SESS#002"},
                update_expression="SET #s = :s",
                expression_attribute_values={":s": "expired"},
                expression_attribute_names={"#s": "status"},
                condition_expression="attribute_exists(PK)",
            )
        assert result["Attributes"]["status"] == "expired"

    def test_update_item_without_optional_params(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            dynamo.Table(TABLE_NAME).put_item(
                Item={"PK": f"TENANT#{TENANT_ID}", "SK": "SESS#003", "invocations": 0}
            )
            db.update_item(
                TABLE_NAME,
                key={"PK": f"TENANT#{TENANT_ID}", "SK": "SESS#003"},
                update_expression="SET invocations = :c",
                expression_attribute_values={":c": 1},
            )

    def test_update_item_cross_tenant_raises_violation(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            db, _ = _make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                db.update_item(
                    TABLE_NAME,
                    key={"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "SESS#001"},
                    update_expression="SET #s = :s",
                    expression_attribute_values={":s": "hacked"},
                    expression_attribute_names={"#s": "status"},
                )
        assert exc_info.value.caller_tenant_id == TENANT_ID


# ===========================================================================
# TenantScopedDynamoDB — delete_item
# ===========================================================================


class TestTenantScopedDynamoDBDeleteItem:
    def test_delete_item_own_tenant_succeeds(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "DEL#001"})
            db.delete_item(TABLE_NAME, {"PK": f"TENANT#{TENANT_ID}", "SK": "DEL#001"})
            item = table.get_item(Key={"PK": f"TENANT#{TENANT_ID}", "SK": "DEL#001"})
        assert "Item" not in item

    def test_delete_item_cross_tenant_raises_violation(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            db, _ = _make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                db.delete_item(TABLE_NAME, {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "DEL#001"})
        assert exc_info.value.caller_tenant_id == TENANT_ID
        assert exc_info.value.tenant_id == OTHER_TENANT_ID
        mock_cw.put_metric_data.assert_called_once()


# ===========================================================================
# TenantScopedDynamoDB — query
# ===========================================================================


class TestTenantScopedDynamoDBQuery:
    def test_query_returns_tenant_items_only(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            # Two items for our tenant
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001"})
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#002"})
            # One item for another tenant (written directly — bypassing lib)
            table.put_item(Item={"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "INV#001"})

            items = db.query(TABLE_NAME)
        assert len(items) == 2
        assert all(i["PK"] == f"TENANT#{TENANT_ID}" for i in items)

    def test_query_with_sk_condition(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        from boto3.dynamodb.conditions import Key

        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001"})
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#002"})
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "SESS#001"})

            items = db.query(TABLE_NAME, sk_condition=Key("SK").begins_with("INV#"))
        assert len(items) == 2

    def test_query_with_limit(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            for i in range(5):
                table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": f"INV#{i:03d}"})

            items = db.query(TABLE_NAME, limit=2)
        assert len(items) == 2

    def test_query_with_scan_index_forward_false(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001"})
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#002"})

            items = db.query(TABLE_NAME, scan_index_forward=False)
        # Reversed order
        assert items[0]["SK"] == "INV#002"
        assert items[1]["SK"] == "INV#001"

    def test_query_with_exclusive_start_key(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            for i in range(3):
                table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": f"INV#{i:03d}"})

            # First page
            first_page = db.query(TABLE_NAME, limit=2)
            # Second page
            second_page = db.query(
                TABLE_NAME,
                exclusive_start_key={"PK": first_page[-1]["PK"], "SK": first_page[-1]["SK"]},
            )
        assert len(second_page) == 1

    def test_query_optional_kwargs_via_mock(self, ctx: TenantContext) -> None:
        """Verify index_name and filter_expression kwargs are forwarded."""
        mock_dynamo = MagicMock()
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.query.return_value = {"Items": []}

        from boto3.dynamodb.conditions import Attr

        db = TenantScopedDynamoDB(ctx, dynamodb_resource=mock_dynamo, cloudwatch_client=MagicMock())
        db.query(
            TABLE_NAME,
            filter_expression=Attr("status").eq("active"),
            index_name="status-index",
        )

        call_kwargs = mock_table.query.call_args.kwargs
        assert "FilterExpression" in call_kwargs
        assert call_kwargs["IndexName"] == "status-index"

    def test_query_empty_result(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = _make_dynamo_db(ctx, cw=mock_cw)
            items = db.query(TABLE_NAME)
        assert items == []

    def test_query_always_uses_caller_partition(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        """query() must never reach other tenants' partitions."""
        with mock_aws():
            db, dynamo = _make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            # Write items for other tenant directly
            table.put_item(Item={"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "INV#001"})

            # Query should return nothing (only our partition)
            items = db.query(TABLE_NAME)
        assert items == []
        mock_cw.put_metric_data.assert_not_called()


# ===========================================================================
# TenantScopedDynamoDB — _validate_pk edge cases
# ===========================================================================


class TestValidatePkEdgeCases:
    def test_missing_pk_key_is_allowed(self, ctx: TenantContext) -> None:
        """A key dict without PK doesn't start with TENANT# — not blocked."""
        mock_dynamo = MagicMock()
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": {"data": "ok"}}
        db = TenantScopedDynamoDB(ctx, dynamodb_resource=mock_dynamo, cloudwatch_client=MagicMock())
        # No PK in key — no check triggered
        result = db.get_item(TABLE_NAME, {"SK": "METADATA"})
        assert result == {"data": "ok"}

    def test_non_string_pk_is_allowed(self, ctx: TenantContext) -> None:
        """A non-string PK value bypasses the TENANT# check."""
        mock_dynamo = MagicMock()
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.get_item.return_value = {"Item": {}}
        db = TenantScopedDynamoDB(ctx, dynamodb_resource=mock_dynamo, cloudwatch_client=MagicMock())
        db.get_item(TABLE_NAME, {"PK": 12345, "SK": "SK"})


# ===========================================================================
# TenantScopedDynamoDB — metric emission failure does not suppress violation
# ===========================================================================


class TestDynamoDBViolationMetricFailure:
    def test_cw_failure_still_raises_violation(self, ctx: TenantContext) -> None:
        failing_cw = MagicMock()
        failing_cw.put_metric_data.side_effect = Exception("CW down")
        with mock_aws():
            db, _ = _make_dynamo_db(ctx, cw=failing_cw)
            with pytest.raises(TenantAccessViolation):
                db.get_item(
                    TABLE_NAME,
                    {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "METADATA"},
                )


# ===========================================================================
# TenantScopedS3 — init
# ===========================================================================


class TestTenantScopedS3Init:
    def test_init_with_injected_clients(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        mock_s3 = MagicMock()
        s3 = TenantScopedS3(ctx, s3_client=mock_s3, cloudwatch_client=mock_cw)
        assert s3._tenant_id == TENANT_ID
        assert s3._prefix == f"tenants/{TENANT_ID}/"

    def test_init_without_injected_clients(self, ctx: TenantContext) -> None:
        with mock_aws():
            s3 = TenantScopedS3(ctx)
        assert s3._tenant_id == TENANT_ID


# ===========================================================================
# TenantScopedS3 — put_object
# ===========================================================================


class TestTenantScopedS3PutObject:
    def test_put_own_prefix_succeeds(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = _make_s3(ctx, cw=mock_cw)
            scoped.put_object(BUCKET, f"tenants/{TENANT_ID}/results/job-1.json", b"data")
            obj = s3.get_object(Bucket=BUCKET, Key=f"tenants/{TENANT_ID}/results/job-1.json")
        assert obj["Body"].read() == b"data"
        mock_cw.put_metric_data.assert_not_called()

    def test_put_with_extra_kwargs(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = _make_s3(ctx, cw=mock_cw)
            scoped.put_object(
                BUCKET,
                f"tenants/{TENANT_ID}/data.txt",
                b"hello",
                ContentType="text/plain",
            )
            head = s3.head_object(Bucket=BUCKET, Key=f"tenants/{TENANT_ID}/data.txt")
        assert head["ContentType"] == "text/plain"

    # -------------------------------------------------------------------
    # REQUIRED coverage assertion: cross-tenant S3 write raises violation
    # -------------------------------------------------------------------
    def test_put_cross_tenant_raises_violation(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.put_object(BUCKET, f"tenants/{OTHER_TENANT_ID}/evil.json", b"evil")
        exc = exc_info.value
        assert exc.caller_tenant_id == TENANT_ID
        assert exc.tenant_id == OTHER_TENANT_ID
        mock_cw.put_metric_data.assert_called_once()

    def test_put_non_tenant_path_raises_violation(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.put_object(BUCKET, "global/config.json", b"data")
        assert exc_info.value.tenant_id == "unknown"


# ===========================================================================
# TenantScopedS3 — get_object
# ===========================================================================


class TestTenantScopedS3GetObject:
    def test_get_own_prefix_succeeds(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = _make_s3(ctx, cw=mock_cw)
            s3.put_object(
                Bucket=BUCKET,
                Key=f"tenants/{TENANT_ID}/results/out.json",
                Body=b"result",
            )
            response = scoped.get_object(BUCKET, f"tenants/{TENANT_ID}/results/out.json")
        assert response["Body"].read() == b"result"

    # -------------------------------------------------------------------
    # REQUIRED coverage assertion: cross-tenant S3 read raises violation
    # -------------------------------------------------------------------
    def test_get_cross_tenant_raises_violation(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.get_object(BUCKET, f"tenants/{OTHER_TENANT_ID}/secret.json")
        exc = exc_info.value
        assert exc.caller_tenant_id == TENANT_ID
        assert exc.tenant_id == OTHER_TENANT_ID

    def test_get_cross_tenant_emits_cloudwatch_metric(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        """REQUIRED: TenantAccessViolation (S3 read) emits CloudWatch metric."""
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation):
                scoped.get_object(BUCKET, f"tenants/{OTHER_TENANT_ID}/secret.json")
        mock_cw.put_metric_data.assert_called_once()
        kwargs = mock_cw.put_metric_data.call_args.kwargs
        assert kwargs["Namespace"] == "platform/security"
        assert kwargs["MetricData"][0]["MetricName"] == "TenantAccessViolation"

    def test_get_non_tenant_path_raises_violation_unknown_tenant(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.get_object(BUCKET, "internal/platform/config.json")
        assert exc_info.value.tenant_id == "unknown"


# ===========================================================================
# TenantScopedS3 — delete_object
# ===========================================================================


class TestTenantScopedS3DeleteObject:
    def test_delete_own_prefix_succeeds(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = _make_s3(ctx, cw=mock_cw)
            key = f"tenants/{TENANT_ID}/file.txt"
            s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
            scoped.delete_object(BUCKET, key)
            # Confirm deletion
            objects = s3.list_objects_v2(Bucket=BUCKET)
            assert objects.get("KeyCount", 0) == 0

    def test_delete_cross_tenant_raises_violation(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation):
                scoped.delete_object(BUCKET, f"tenants/{OTHER_TENANT_ID}/file.txt")
        mock_cw.put_metric_data.assert_called_once()


# ===========================================================================
# TenantScopedS3 — list_objects
# ===========================================================================


class TestTenantScopedS3ListObjects:
    def test_list_objects_own_prefix(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = _make_s3(ctx, cw=mock_cw)
            for i in range(3):
                s3.put_object(
                    Bucket=BUCKET,
                    Key=f"tenants/{TENANT_ID}/results/job-{i}.json",
                    Body=b"{}",
                )
            # Also put an item for another tenant
            s3.put_object(
                Bucket=BUCKET,
                Key=f"tenants/{OTHER_TENANT_ID}/results/job-0.json",
                Body=b"{}",
            )
            items = scoped.list_objects(BUCKET)
        assert len(items) == 3
        assert all(f"tenants/{TENANT_ID}/" in item["Key"] for item in items)

    def test_list_objects_with_sub_prefix(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = _make_s3(ctx, cw=mock_cw)
            s3.put_object(Bucket=BUCKET, Key=f"tenants/{TENANT_ID}/results/job-1.json", Body=b"{}")
            s3.put_object(Bucket=BUCKET, Key=f"tenants/{TENANT_ID}/logs/run.log", Body=b"log")
            items = scoped.list_objects(BUCKET, prefix="results/")
        assert len(items) == 1
        assert "results/" in items[0]["Key"]

    def test_list_objects_empty(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            items = scoped.list_objects(BUCKET)
        assert items == []


# ===========================================================================
# TenantScopedS3 — generate_presigned_url
# ===========================================================================


class TestTenantScopedS3PresignedUrl:
    def test_presigned_url_own_prefix(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            url = scoped.generate_presigned_url(BUCKET, f"tenants/{TENANT_ID}/results/out.json")
        assert isinstance(url, str)
        assert TENANT_ID in url

    def test_presigned_url_custom_method(self, ctx: TenantContext, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            url = scoped.generate_presigned_url(
                BUCKET,
                f"tenants/{TENANT_ID}/upload.zip",
                client_method="put_object",
                expires_in=600,
            )
        assert isinstance(url, str)

    def test_presigned_url_cross_tenant_raises_violation(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.generate_presigned_url(BUCKET, f"tenants/{OTHER_TENANT_ID}/secret.json")
        assert exc_info.value.caller_tenant_id == TENANT_ID
        mock_cw.put_metric_data.assert_called_once()


# ===========================================================================
# TenantScopedS3 — validate_key: tenants/ prefix but short path
# ===========================================================================


class TestS3ValidateKeyEdgeCases:
    def test_tenants_prefix_but_no_tenant_segment(
        self, ctx: TenantContext, mock_cw: MagicMock
    ) -> None:
        """'tenants/' with no tenant segment → target_tenant_id='tenants'."""
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.get_object(BUCKET, "tenants/")
        # Key is "tenants/" — split gives ["tenants", ""] — parts[1] = ""
        assert exc_info.value.tenant_id == ""

    def test_metric_emission_failure_still_raises_violation(self, ctx: TenantContext) -> None:
        failing_cw = MagicMock()
        failing_cw.put_metric_data.side_effect = Exception("CW down")
        with mock_aws():
            scoped, _ = _make_s3(ctx, cw=failing_cw)
            with pytest.raises(TenantAccessViolation):
                scoped.get_object(BUCKET, f"tenants/{OTHER_TENANT_ID}/data.json")
