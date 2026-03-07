"""Unit tests for scripts/build_layer.py (TASK-034)."""

from __future__ import annotations

import importlib.util
import io
import sys
import zipfile
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws


def _load_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "build_layer_script", repo_root / "scripts" / "build_layer.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


bl: Any = _load_module()
_REGION = "eu-west-2"


def _create_bucket(bucket_name: str) -> None:
    s3 = boto3.client("s3", region_name=_REGION)
    s3.create_bucket(
        Bucket=bucket_name,
        CreateBucketConfiguration={"LocationConstraint": _REGION},
    )


def _write_arm64_deps_fixture(target_dir: Path) -> None:
    package_dir = target_dir / "sample"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "native.cpython-312-aarch64-linux-gnu.so").write_bytes(b"arm64")

    dist_info = target_dir / "sample-1.0.0.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: test\nTag: cp312-cp312-manylinux2014_aarch64\n",
        encoding="utf-8",
    )


def test_parse_args_positional_and_env() -> None:
    args = bl.parse_args(["echo-agent", "--env", "dev"])
    assert args.agent_name == "echo-agent"
    assert args.env == "dev"


def test_parse_args_rejects_invalid_env() -> None:
    with pytest.raises(SystemExit):
        bl.parse_args(["echo-agent", "--env", "production"])


def test_build_dependencies_uses_required_arm64_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(command: list[str], *, cwd: Path, check: bool) -> None:
        captured["command"] = command
        captured["cwd"] = cwd
        captured["check"] = check

    monkeypatch.setattr(bl.subprocess, "run", _fake_run)
    target_dir = tmp_path / "deps"
    bl.build_dependencies(["boto3>=1.37.0"], target_dir)

    command = captured["command"]
    assert command[:3] == ["uv", "pip", "install"]
    assert "--python-platform" in command
    assert bl.PYTHON_PLATFORM in command
    assert "--python-version" in command
    assert bl.PYTHON_VERSION in command
    assert "--target" in command
    assert str(target_dir) in command
    assert "--only-binary=:all:" in command
    assert "boto3>=1.37.0" in command
    assert captured["cwd"] == bl.REPO_ROOT
    assert captured["check"] is True


def test_verify_arm64_zip_accepts_arm64_wheel_and_binary(tmp_path: Path) -> None:
    deps_dir = tmp_path / "deps"
    _write_arm64_deps_fixture(deps_dir)
    zip_path = tmp_path / "deps.zip"

    bl.create_layer_zip(deps_dir, zip_path)
    bl.verify_arm64_zip(zip_path)


def test_verify_arm64_zip_rejects_x86_wheel(tmp_path: Path) -> None:
    deps_dir = tmp_path / "deps"
    deps_dir.mkdir(parents=True, exist_ok=True)

    dist_info = deps_dir / "bad-1.0.0.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\nTag: cp312-cp312-manylinux2014_x86_64\n",
        encoding="utf-8",
    )
    (deps_dir / "bad").mkdir(parents=True, exist_ok=True)
    (deps_dir / "bad" / "module.cpython-312-x86_64-linux-gnu.so").write_bytes(b"x86")

    zip_path = tmp_path / "deps-bad.zip"
    bl.create_layer_zip(deps_dir, zip_path)

    with pytest.raises(RuntimeError, match="Non-arm64"):
        bl.verify_arm64_zip(zip_path)


@mock_aws
def test_run_uploads_zip_and_updates_ssm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bucket = "platform-agent-layer-dev"
    _create_bucket(bucket)
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("PLATFORM_LAYER_BUCKET", bucket)
    monkeypatch.setattr(bl, "BUILD_DIR", tmp_path / ".build")

    def _fake_build_dependencies(_deps: list[str], target_dir: Path) -> None:
        _write_arm64_deps_fixture(target_dir)

    monkeypatch.setattr(bl, "build_dependencies", _fake_build_dependencies)

    exit_code = bl.run("echo-agent", "dev")
    assert exit_code == 0

    expected_hash = bl.compute_dependency_hash(bl.read_agent_deps("echo-agent"))
    expected_key = f"layers/echo-agent-deps-{expected_hash}.zip"

    ssm = boto3.client("ssm", region_name=_REGION)
    hash_value = ssm.get_parameter(Name="/platform/layers/echo-agent/hash")["Parameter"]["Value"]
    key_value = ssm.get_parameter(Name="/platform/layers/echo-agent/s3-key")["Parameter"]["Value"]
    assert hash_value == expected_hash
    assert key_value == expected_key

    s3 = boto3.client("s3", region_name=_REGION)
    obj = s3.get_object(Bucket=bucket, Key=expected_key)
    body = obj["Body"].read()
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        wheel_text = archive.read("sample-1.0.0.dist-info/WHEEL").decode("utf-8")
        assert "manylinux2014_aarch64" in wheel_text
