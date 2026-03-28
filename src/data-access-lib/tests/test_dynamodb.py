from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from data_access import TenantAccessViolation, TenantScopedDynamoDB
from moto import mock_aws

from .conftest import OTHER_TENANT_ID, TABLE_NAME, TENANT_ID, make_dynamo_db


class TestTenantScopedDynamoDBInit:
    def test_init_with_injected_clients(self, ctx, mock_cw: MagicMock) -> None:
        mock_dynamo = MagicMock()
        db = TenantScopedDynamoDB(ctx, dynamodb_resource=mock_dynamo, cloudwatch_client=mock_cw)
        assert db._tenant_id == TENANT_ID
        assert db._app_id == "app-001"

    def test_init_without_injected_clients_uses_env_region(self, ctx) -> None:
        with mock_aws():
            db = TenantScopedDynamoDB(ctx)
        assert db._tenant_id == TENANT_ID


class TestTenantScopedDynamoDBGetItem:
    def test_get_item_own_tenant_found(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            dynamo.Table(TABLE_NAME).put_item(
                Item={"PK": f"TENANT#{TENANT_ID}", "SK": "METADATA", "data": "hello"}
            )
            item = db.get_item(TABLE_NAME, {"PK": f"TENANT#{TENANT_ID}", "SK": "METADATA"})
        assert item is not None
        assert item["data"] == "hello"

    def test_get_item_own_tenant_not_found_returns_none(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = make_dynamo_db(ctx, cw=mock_cw)
            result = db.get_item(TABLE_NAME, {"PK": f"TENANT#{TENANT_ID}", "SK": "MISSING"})
        assert result is None

    def test_get_item_non_tenant_pk_allowed(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            dynamo.Table(TABLE_NAME).put_item(
                Item={"PK": "AGENT#echo-agent", "SK": "VERSION#1.0.0"}
            )
            item = db.get_item(TABLE_NAME, {"PK": "AGENT#echo-agent", "SK": "VERSION#1.0.0"})
        assert item is not None
        mock_cw.put_metric_data.assert_not_called()

    def test_get_item_cross_tenant_raises_violation(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                db.get_item(
                    TABLE_NAME,
                    {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "METADATA"},
                )
        exc = exc_info.value
        assert exc.caller_tenant_id == TENANT_ID
        assert exc.tenant_id == OTHER_TENANT_ID

    def test_get_item_cross_tenant_emits_cloudwatch_metric(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation):
                db.get_item(
                    TABLE_NAME,
                    {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "METADATA"},
                )
        mock_cw.put_metric_data.assert_called_once()
        kwargs = mock_cw.put_metric_data.call_args.kwargs
        assert kwargs["Namespace"] == "platform/security"
        assert kwargs["MetricData"][0]["MetricName"] == "TenantAccessViolation"


class TestTenantScopedDynamoDBPutItem:
    def test_put_item_own_tenant_succeeds(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            db.put_item(
                TABLE_NAME,
                {"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001", "tokens": 100},
            )
            item = dynamo.Table(TABLE_NAME).get_item(
                Key={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001"}
            )
        assert item["Item"]["tokens"] == 100
        mock_cw.put_metric_data.assert_not_called()

    def test_put_item_cross_tenant_raises_violation(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                db.put_item(
                    TABLE_NAME,
                    {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "INV#001"},
                )
        exc = exc_info.value
        assert exc.caller_tenant_id == TENANT_ID
        assert exc.tenant_id == OTHER_TENANT_ID

    def test_put_item_cross_tenant_emits_cloudwatch_metric(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation):
                db.put_item(
                    TABLE_NAME,
                    {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "INV#001"},
                )
        mock_cw.put_metric_data.assert_called_once()

    def test_put_item_non_tenant_pk_allowed(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = make_dynamo_db(ctx, cw=mock_cw)
            db.put_item(TABLE_NAME, {"PK": "LOCK#failover", "SK": "METADATA"})
        mock_cw.put_metric_data.assert_not_called()


class TestTenantScopedDynamoDBUpdateItem:
    def test_update_item_own_tenant_succeeds(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
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

    def test_update_item_with_condition_expression(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
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

    def test_update_item_without_optional_params(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            dynamo.Table(TABLE_NAME).put_item(
                Item={"PK": f"TENANT#{TENANT_ID}", "SK": "SESS#003", "invocations": 0}
            )
            db.update_item(
                TABLE_NAME,
                key={"PK": f"TENANT#{TENANT_ID}", "SK": "SESS#003"},
                update_expression="SET invocations = :c",
                expression_attribute_values={":c": 1},
            )

    def test_update_item_cross_tenant_raises_violation(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                db.update_item(
                    TABLE_NAME,
                    key={"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "SESS#001"},
                    update_expression="SET #s = :s",
                    expression_attribute_values={":s": "hacked"},
                    expression_attribute_names={"#s": "status"},
                )
        assert exc_info.value.caller_tenant_id == TENANT_ID


class TestTenantScopedDynamoDBDeleteItem:
    def test_delete_item_own_tenant_succeeds(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "DEL#001"})
            db.delete_item(TABLE_NAME, {"PK": f"TENANT#{TENANT_ID}", "SK": "DEL#001"})
            item = table.get_item(Key={"PK": f"TENANT#{TENANT_ID}", "SK": "DEL#001"})
        assert "Item" not in item

    def test_delete_item_cross_tenant_raises_violation(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = make_dynamo_db(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                db.delete_item(TABLE_NAME, {"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "DEL#001"})
        assert exc_info.value.caller_tenant_id == TENANT_ID
        assert exc_info.value.tenant_id == OTHER_TENANT_ID
        mock_cw.put_metric_data.assert_called_once()


class TestTenantScopedDynamoDBQuery:
    def test_query_returns_tenant_items_only(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001"})
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#002"})
            table.put_item(Item={"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "INV#001"})
            result = db.query(TABLE_NAME)
        assert len(result.items) == 2
        assert all(i["PK"] == f"TENANT#{TENANT_ID}" for i in result.items)

    def test_query_with_sk_condition(self, ctx, mock_cw: MagicMock) -> None:
        from boto3.dynamodb.conditions import Key

        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001"})
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#002"})
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "SESS#001"})
            result = db.query(TABLE_NAME, sk_condition=Key("SK").begins_with("INV#"))
        assert len(result.items) == 2

    def test_query_with_limit(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            for i in range(5):
                table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": f"INV#{i:03d}"})
            result = db.query(TABLE_NAME, limit=2)
        assert len(result.items) == 2

    def test_query_with_scan_index_forward_false(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001"})
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#002"})
            result = db.query(TABLE_NAME, scan_index_forward=False)
        assert result.items[0]["SK"] == "INV#002"
        assert result.items[1]["SK"] == "INV#001"

    def test_query_with_exclusive_start_key(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            for i in range(3):
                table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": f"INV#{i:03d}"})
            result1 = db.query(TABLE_NAME, limit=2)
            assert result1.last_evaluated_key is not None
            result2 = db.query(
                TABLE_NAME,
                exclusive_start_key=result1.last_evaluated_key,
            )
        assert len(result2.items) == 1
        assert result2.last_evaluated_key is None

    def test_query_optional_kwargs_via_mock(self, ctx) -> None:
        from boto3.dynamodb.conditions import Attr

        mock_dynamo = MagicMock()
        mock_table = MagicMock()
        mock_dynamo.Table.return_value = mock_table
        mock_table.query.return_value = {"Items": []}
        db = TenantScopedDynamoDB(ctx, dynamodb_resource=mock_dynamo, cloudwatch_client=MagicMock())
        db.query(
            TABLE_NAME,
            filter_expression=Attr("status").eq("active"),
            index_name="status-index",
        )
        call_kwargs = mock_table.query.call_args.kwargs
        assert "FilterExpression" in call_kwargs
        assert call_kwargs["IndexName"] == "status-index"

    def test_query_empty_result(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, _ = make_dynamo_db(ctx, cw=mock_cw)
            result = db.query(TABLE_NAME)
        assert result.items == []

    def test_query_always_uses_caller_partition(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            table.put_item(Item={"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "INV#001"})
            result = db.query(TABLE_NAME)
        assert result.items == []
        mock_cw.put_metric_data.assert_not_called()


class TestTenantScopedDynamoDBScan:
    def test_scan_returns_all_items_regardless_of_tenant(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001"})
            table.put_item(Item={"PK": f"TENANT#{OTHER_TENANT_ID}", "SK": "INV#002"})
            result = db.scan(TABLE_NAME)
        assert len(result.items) == 2

    def test_scan_with_limit_and_pagination(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            for i in range(5):
                table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": f"INV#{i:03d}"})
            result1 = db.scan(TABLE_NAME, limit=2)
            assert len(result1.items) == 2
            assert result1.last_evaluated_key is not None
            result2 = db.scan(TABLE_NAME, limit=2, exclusive_start_key=result1.last_evaluated_key)
            assert len(result2.items) == 2
            assert result2.last_evaluated_key is not None
            result3 = db.scan(TABLE_NAME, limit=2, exclusive_start_key=result2.last_evaluated_key)
            assert len(result3.items) == 1
            assert result3.last_evaluated_key is None

    def test_scan_with_filter_expression(self, ctx, mock_cw: MagicMock) -> None:
        from boto3.dynamodb.conditions import Attr

        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#001", "status": "ok"})
            table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": "INV#002", "status": "fail"})
            result = db.scan(TABLE_NAME, filter_expression=Attr("status").eq("ok"))
        assert len(result.items) == 1
        assert result.items[0]["SK"] == "INV#001"

    def test_scan_all_paginates(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            for i in range(5):
                table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": f"INV#{i:03d}"})
            original_scan = db.scan

            def mock_scan_side_effect(*args, **kwargs):
                return original_scan(*args, **{**kwargs, "limit": 2})

            with patch.object(db, "scan", side_effect=mock_scan_side_effect) as mock_s:
                items = db.scan_all(TABLE_NAME)
                assert len(items) == 5
                assert mock_s.call_count == 3


class TestTenantScopedDynamoDBQueryAll:
    def test_query_all_paginates(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            db, dynamo = make_dynamo_db(ctx, cw=mock_cw)
            table = dynamo.Table(TABLE_NAME)
            for i in range(5):
                table.put_item(Item={"PK": f"TENANT#{TENANT_ID}", "SK": f"INV#{i:03d}"})
            original_query = db.query

            def mock_query_side_effect(*args, **kwargs):
                return original_query(*args, **{**kwargs, "limit": 2})

            with patch.object(db, "query", side_effect=mock_query_side_effect) as mock_q:
                items = db.query_all(TABLE_NAME)
                assert len(items) == 5
                assert mock_q.call_count == 3
