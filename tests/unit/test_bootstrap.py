"""Unit tests for scripts/bootstrap.py (TASK-028)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws


def _load_bootstrap_module() -> object:
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "bootstrap_script", repo_root / "scripts" / "bootstrap.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


bootstrap: Any = _load_bootstrap_module()
_REGION = "eu-west-2"


def _ctx() -> object:
    return bootstrap.BootstrapContext(
        env="dev",
        aws_region=_REGION,
        home_region=_REGION,
        runtime_region="eu-west-1",
        fallback_region="eu-central-1",
        account_id="111122223333",
        caller_arn="arn:aws:iam::111122223333:user/bootstrap-user",
        report_bucket="platform-bootstrap-reports-dev",
        report_key="bootstrap-report.json",
    )


def test_parse_args_supports_first_deploy_step() -> None:
    args = bootstrap.parse_args(["--step", "first-deploy", "--env", "dev"])
    assert args.step == "first-deploy"
    assert args.env == "dev"


def test_build_first_deploy_command_targets_supported_bootstrap_stacks_only() -> None:
    command = bootstrap.build_first_deploy_command(_ctx())

    assert command[:3] == ["npx", "cdk", "deploy"]
    assert "--all" not in command
    assert command[3:8] == [
        "platform-network-dev",
        "platform-identity-dev",
        "platform-core-dev",
        "platform-tenant-stub-dev",
        "platform-observability-dev",
    ]
    assert "platform-agentcore-dev" not in command
    assert command[-4:] == ["--context", "env=dev", "--require-approval", "never"]


def test_validate_first_deploy_checks_home_region_bootstrap_stacks_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def _fake_client(service_name: str, *, region_name: str) -> str:
        assert service_name == "cloudformation"
        return region_name

    def _fake_stack_status(client: str, stack_name: str) -> str:
        calls.append((client, stack_name))
        return "CREATE_COMPLETE"

    monkeypatch.setattr(bootstrap.boto3, "client", _fake_client)
    monkeypatch.setattr(bootstrap, "_stack_status", _fake_stack_status)

    result = bootstrap.validate_first_deploy(_ctx())

    assert result == {
        "homeRegion": {
            "platform-network-dev": "CREATE_COMPLETE",
            "platform-identity-dev": "CREATE_COMPLETE",
            "platform-core-dev": "CREATE_COMPLETE",
            "platform-tenant-stub-dev": "CREATE_COMPLETE",
            "platform-observability-dev": "CREATE_COMPLETE",
        }
    }
    assert calls == [
        ("eu-west-2", "platform-network-dev"),
        ("eu-west-2", "platform-identity-dev"),
        ("eu-west-2", "platform-core-dev"),
        ("eu-west-2", "platform-tenant-stub-dev"),
        ("eu-west-2", "platform-observability-dev"),
    ]


@mock_aws
def test_upsert_secret_create_then_update() -> None:
    client = boto3.client("secretsmanager", region_name=_REGION)
    secret_name = "platform/dev/entra/client-id"  # pragma: allowlist secret

    first = bootstrap.upsert_secret(
        client,
        secret_name=secret_name,
        secret_value="value-1",  # pragma: allowlist secret
        description="test",
    )
    second = bootstrap.upsert_secret(
        client,
        secret_name=secret_name,
        secret_value="value-2",  # pragma: allowlist secret
        description="test",
    )

    final_value = client.get_secret_value(SecretId=secret_name)["SecretString"]
    assert first == "created"
    assert second in {"created", "updated"}
    assert final_value == "value-2"


@mock_aws
def test_report_roundtrip_s3() -> None:
    ctx = _ctx()
    s3_client = boto3.client("s3", region_name=_REGION)

    bootstrap.ensure_report_bucket(s3_client, ctx)
    report = bootstrap.initial_report(ctx)
    report["steps"].append({"step": "seed-secrets", "status": "passed"})

    uri = bootstrap.persist_report(s3_client, ctx, report)
    loaded = bootstrap.load_report(s3_client, ctx)

    assert uri == "s3://platform-bootstrap-reports-dev/bootstrap-report.json"
    assert loaded["steps"][0]["step"] == "seed-secrets"
    assert loaded["steps"][0]["status"] == "passed"


@mock_aws
def test_execute_step_failure_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx()
    s3_client = boto3.client("s3", region_name=_REGION)

    bootstrap.ensure_report_bucket(s3_client, ctx)
    report = bootstrap.initial_report(ctx)

    def _raise(_: object) -> dict[str, str]:
        raise RuntimeError("boom")

    def _validate(_: object) -> dict[str, str]:
        return {"ok": "yes"}

    monkeypatch.setitem(bootstrap.STEP_HANDLERS, "seed-secrets", (_raise, _validate))

    with pytest.raises(RuntimeError, match="boom"):
        bootstrap.execute_step(
            step_name="seed-secrets",
            ctx=ctx,
            report=report,
            s3_client=s3_client,
        )

    loaded = bootstrap.load_report(s3_client, ctx)
    last = loaded["steps"][-1]
    assert last["step"] == "seed-secrets"
    assert last["status"] == "failed"
    assert last["details"]["errorType"] == "RuntimeError"
