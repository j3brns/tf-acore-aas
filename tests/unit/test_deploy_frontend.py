"""Unit tests for scripts/deploy_frontend.py (issue #232)."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws


def _load_module() -> Any:
    """Load the script as a module."""
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "deploy_frontend_script", repo_root / "scripts" / "deploy_frontend.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Load the script module once
df: Any = _load_module()
_REGION = "eu-west-2"


def test_parse_args_env() -> None:
    """Verify CLI argument parsing."""
    args = df.parse_args(["--env", "staging"])
    assert args.env == "staging"


@mock_aws
def test_deploy_spa_uploads_files_with_correct_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify S3 uploads, content types, and cache-control headers."""
    bucket = "platform-spa-bucket-dev"
    dist_id = "EDIST12345"

    # Setup mock AWS resources
    s3 = boto3.client("s3", region_name=_REGION)
    s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": _REGION})

    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(Name="/platform/spa/dev/bucket-name", Value=bucket, Type="String")
    ssm.put_parameter(Name="/platform/spa/dev/distribution-id", Value=dist_id, Type="String")

    # Create fake SPA dist directory
    dist_dir = tmp_path / "spa" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (dist_dir / "assets").mkdir()
    (dist_dir / "assets" / "main-hash123.js").write_text("console.log(1)", encoding="utf-8")
    (dist_dir / "assets" / "style-hash123.css").write_text("body { color: red; }", encoding="utf-8")
    (dist_dir / "favicon.ico").write_bytes(b"fake-icon-data")

    # Configure script mocks
    monkeypatch.setattr(df, "SPA_DIST", dist_dir)
    monkeypatch.setenv("AWS_REGION", _REGION)

    # Execute deployment
    df.deploy_spa("dev")

    # Verify index.html (no-cache)
    obj_index = s3.get_object(Bucket=bucket, Key="index.html")
    assert obj_index["ContentType"] in ("text/html", "application/octet-stream")  # depends on env
    assert obj_index["CacheControl"] == "no-cache, no-store, must-revalidate"

    # Verify hashed JS asset (immutable)
    obj_js = s3.get_object(Bucket=bucket, Key="assets/main-hash123.js")
    assert obj_js["ContentType"] == "application/javascript"
    assert obj_js["CacheControl"] == "public, max-age=31536000, immutable"

    # Verify hashed CSS asset (immutable)
    obj_css = s3.get_object(Bucket=bucket, Key="assets/style-hash123.css")
    assert obj_css["ContentType"] == "text/css"
    assert obj_css["CacheControl"] == "public, max-age=31536000, immutable"

    # Verify static asset (default short cache)
    obj_ico = s3.get_object(Bucket=bucket, Key="favicon.ico")
    assert obj_ico["CacheControl"] == "public, max-age=3600"


@mock_aws
def test_deploy_spa_handles_missing_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify script exits gracefully if dist directory is missing."""
    monkeypatch.setattr(df, "SPA_DIST", tmp_path / "nonexistent")
    monkeypatch.setenv("AWS_REGION", _REGION)

    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(Name="/platform/spa/dev/bucket-name", Value="some-bucket", Type="String")
    ssm.put_parameter(Name="/platform/spa/dev/distribution-id", Value="some-dist", Type="String")

    with pytest.raises(SystemExit) as excinfo:
        df.deploy_spa("dev")
    assert excinfo.value.code == 1


@mock_aws
def test_deploy_spa_requests_cloudfront_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify CloudFront invalidation is requested."""
    bucket = "platform-spa-bucket-dev"

    s3 = boto3.client("s3", region_name=_REGION)
    s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": _REGION})

    # In moto, we need a distribution to exist for invalidation to succeed
    cf = boto3.client("cloudfront", region_name=_REGION)
    dist = cf.create_distribution(
        DistributionConfig={
            "CallerReference": str(time.time()),
            "Enabled": True,
            "DefaultCacheBehavior": {
                "TargetOriginId": "test",
                "ForwardedValues": {"QueryString": False, "Cookies": {"Forward": "none"}},
                "TrustedSigners": {"Enabled": False, "Quantity": 0},
                "ViewerProtocolPolicy": "allow-all",
                "MinTTL": 0,
            },
            "Origins": {
                "Quantity": 1,
                "Items": [
                    {
                        "Id": "test",
                        "DomainName": f"{bucket}.s3.amazonaws.com",
                        "S3OriginConfig": {"OriginAccessIdentity": ""},
                    }
                ],
            },
            "Comment": "test",
        }
    )
    dist_id = dist["Distribution"]["Id"]

    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(Name="/platform/spa/dev/bucket-name", Value=bucket, Type="String")
    ssm.put_parameter(Name="/platform/spa/dev/distribution-id", Value=dist_id, Type="String")

    dist_dir = tmp_path / "spa" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("test", encoding="utf-8")

    monkeypatch.setattr(df, "SPA_DIST", dist_dir)
    monkeypatch.setenv("AWS_REGION", _REGION)

    df.deploy_spa("dev")

    invalidations = cf.list_invalidations(DistributionId=dist_id)
    # print(invalidations)
    assert len(invalidations.get("InvalidationList", {}).get("Items", [])) == 1
