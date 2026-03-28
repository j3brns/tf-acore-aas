from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from data_access import TenantAccessViolation, TenantScopedS3
from moto import mock_aws

from .conftest import BUCKET, OTHER_TENANT_ID, TENANT_ID, make_s3


class TestTenantScopedS3Init:
    def test_init_with_injected_clients(self, ctx, mock_cw: MagicMock) -> None:
        mock_s3 = MagicMock()
        s3 = TenantScopedS3(ctx, s3_client=mock_s3, cloudwatch_client=mock_cw)
        assert s3._tenant_id == TENANT_ID
        assert s3._prefix == f"tenants/{TENANT_ID}/"

    def test_init_without_injected_clients(self, ctx) -> None:
        with mock_aws():
            s3 = TenantScopedS3(ctx)
        assert s3._tenant_id == TENANT_ID


class TestTenantScopedS3PutObject:
    def test_put_own_prefix_succeeds(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = make_s3(ctx, cw=mock_cw)
            scoped.put_object(BUCKET, f"tenants/{TENANT_ID}/results/job-1.json", b"data")
            obj = s3.get_object(Bucket=BUCKET, Key=f"tenants/{TENANT_ID}/results/job-1.json")
        assert obj["Body"].read() == b"data"
        mock_cw.put_metric_data.assert_not_called()

    def test_put_with_extra_kwargs(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = make_s3(ctx, cw=mock_cw)
            scoped.put_object(
                BUCKET,
                f"tenants/{TENANT_ID}/data.txt",
                b"hello",
                ContentType="text/plain",
            )
            head = s3.head_object(Bucket=BUCKET, Key=f"tenants/{TENANT_ID}/data.txt")
        assert head["ContentType"] == "text/plain"

    def test_put_cross_tenant_raises_violation(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.put_object(BUCKET, f"tenants/{OTHER_TENANT_ID}/evil.json", b"evil")
        exc = exc_info.value
        assert exc.caller_tenant_id == TENANT_ID
        assert exc.tenant_id == OTHER_TENANT_ID
        mock_cw.put_metric_data.assert_called_once()

    def test_put_non_tenant_path_raises_violation(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.put_object(BUCKET, "global/config.json", b"data")
        assert exc_info.value.tenant_id == "unknown"


class TestTenantScopedS3GetObject:
    def test_get_own_prefix_succeeds(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = make_s3(ctx, cw=mock_cw)
            s3.put_object(
                Bucket=BUCKET,
                Key=f"tenants/{TENANT_ID}/results/out.json",
                Body=b"result",
            )
            response = scoped.get_object(BUCKET, f"tenants/{TENANT_ID}/results/out.json")
        assert response["Body"].read() == b"result"

    def test_get_cross_tenant_raises_violation(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.get_object(BUCKET, f"tenants/{OTHER_TENANT_ID}/secret.json")
        exc = exc_info.value
        assert exc.caller_tenant_id == TENANT_ID
        assert exc.tenant_id == OTHER_TENANT_ID

    def test_get_cross_tenant_emits_cloudwatch_metric(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation):
                scoped.get_object(BUCKET, f"tenants/{OTHER_TENANT_ID}/secret.json")
        mock_cw.put_metric_data.assert_called_once()
        kwargs = mock_cw.put_metric_data.call_args.kwargs
        assert kwargs["Namespace"] == "platform/security"
        assert kwargs["MetricData"][0]["MetricName"] == "TenantAccessViolation"

    def test_get_non_tenant_path_raises_violation_unknown_tenant(
        self, ctx, mock_cw: MagicMock
    ) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.get_object(BUCKET, "internal/platform/config.json")
        assert exc_info.value.tenant_id == "unknown"


class TestTenantScopedS3DeleteObject:
    def test_delete_own_prefix_succeeds(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = make_s3(ctx, cw=mock_cw)
            key = f"tenants/{TENANT_ID}/file.txt"
            s3.put_object(Bucket=BUCKET, Key=key, Body=b"x")
            scoped.delete_object(BUCKET, key)
            objects = s3.list_objects_v2(Bucket=BUCKET)
            assert objects.get("KeyCount", 0) == 0

    def test_delete_cross_tenant_raises_violation(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation):
                scoped.delete_object(BUCKET, f"tenants/{OTHER_TENANT_ID}/file.txt")
        mock_cw.put_metric_data.assert_called_once()


class TestTenantScopedS3ListObjects:
    def test_list_objects_own_prefix(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = make_s3(ctx, cw=mock_cw)
            for i in range(3):
                s3.put_object(
                    Bucket=BUCKET,
                    Key=f"tenants/{TENANT_ID}/results/job-{i}.json",
                    Body=b"{}",
                )
            s3.put_object(
                Bucket=BUCKET,
                Key=f"tenants/{OTHER_TENANT_ID}/results/job-0.json",
                Body=b"{}",
            )
            items = scoped.list_objects(BUCKET)
        assert len(items) == 3
        assert all(f"tenants/{TENANT_ID}/" in item["Key"] for item in items)

    def test_list_objects_with_sub_prefix(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, s3 = make_s3(ctx, cw=mock_cw)
            s3.put_object(Bucket=BUCKET, Key=f"tenants/{TENANT_ID}/results/job-1.json", Body=b"{}")
            s3.put_object(Bucket=BUCKET, Key=f"tenants/{TENANT_ID}/logs/run.log", Body=b"log")
            items = scoped.list_objects(BUCKET, prefix="results/")
        assert len(items) == 1
        assert "results/" in items[0]["Key"]

    def test_list_objects_empty(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            items = scoped.list_objects(BUCKET)
        assert items == []


class TestTenantScopedS3PresignedUrl:
    def test_presigned_url_own_prefix(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            url = scoped.generate_presigned_url(BUCKET, f"tenants/{TENANT_ID}/results/out.json")
        assert isinstance(url, str)
        assert TENANT_ID in url

    def test_presigned_url_custom_method(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            url = scoped.generate_presigned_url(
                BUCKET,
                f"tenants/{TENANT_ID}/upload.zip",
                client_method="put_object",
                expires_in=600,
            )
        assert isinstance(url, str)

    def test_presigned_url_cross_tenant_raises_violation(self, ctx, mock_cw: MagicMock) -> None:
        with mock_aws():
            scoped, _ = make_s3(ctx, cw=mock_cw)
            with pytest.raises(TenantAccessViolation) as exc_info:
                scoped.generate_presigned_url(BUCKET, f"tenants/{OTHER_TENANT_ID}/secret.json")
        assert exc_info.value.caller_tenant_id == TENANT_ID
        mock_cw.put_metric_data.assert_called_once()
