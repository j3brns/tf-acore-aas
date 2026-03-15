"""Unit tests for package_agent.py, deploy_agent.py, and register_agent.py (TASK-035)."""

import importlib.util
import sys
import types
import zipfile
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_module(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


package_agent = _load_module("package_agent")
build_layer = _load_module("build_layer")
deploy_agent = _load_module("deploy_agent")
register_agent = _load_module("register_agent")

_REGION = "eu-west-2"


class _FakeAgentCoreClient:
    def __init__(self, *, response: dict[str, Any] | None = None, error: Exception | None = None):
        self._response = response or {}
        self._error = error

    def update_agent_code(self, **_: Any) -> dict[str, Any]:
        if self._error is not None:
            raise self._error
        return self._response


def _patch_deploy_agentcore(monkeypatch, fake_client: _FakeAgentCoreClient) -> None:
    real_client = boto3.client

    def _client(service_name: str, *args: Any, **kwargs: Any) -> Any:
        if service_name == "bedrock-agentcore":
            return fake_client
        return real_client(service_name, *args, **kwargs)

    monkeypatch.setattr(deploy_agent, "boto3", types.SimpleNamespace(client=_client))


# ---------------------------------------------------------------------------
# package_agent.py tests
# ---------------------------------------------------------------------------


def test_package_agent_creates_zip(tmp_path, monkeypatch):
    # Setup fake agent dir
    agent_name = "test-agent"
    # Monkeypatch REPO_ROOT in package_agent
    monkeypatch.setattr(package_agent, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(package_agent, "BUILD_DIR", tmp_path / ".build")

    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "handler.py").write_text("print('hello')")
    (agent_dir / "pyproject.toml").write_text("[project]\nname='test-agent'")
    (agent_dir / "__pycache__").mkdir()
    (agent_dir / "__pycache__" / "test.pyc").write_text("binary")

    package_agent.package_agent(agent_name)

    zip_path = tmp_path / ".build" / f"{agent_name}-code.zip"
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert "handler.py" in names
        assert "pyproject.toml" in names
        assert "__pycache__/test.pyc" not in names


# ---------------------------------------------------------------------------
# deploy_agent.py tests
# ---------------------------------------------------------------------------


def _prepare_deploy_agent_fixture(tmp_path, monkeypatch) -> tuple[str, str, str]:
    monkeypatch.setenv("AWS_REGION", _REGION)

    monkeypatch.setattr(deploy_agent, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(deploy_agent, "BUILD_DIR", tmp_path / ".build")

    agent_name = "echo-agent"
    env = "dev"
    bucket_name = "platform-artifacts-dev"
    monkeypatch.setenv("PLATFORM_LAYER_BUCKET", bucket_name)

    # Setup S3 bucket
    s3 = boto3.client("s3", region_name=_REGION)
    s3.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": _REGION})

    # Create fake zip
    build_dir = tmp_path / ".build"
    build_dir.mkdir()
    zip_path = build_dir / f"{agent_name}-code.zip"
    zip_path.write_text("fake zip content")

    # Setup SSM for deps-key
    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(
        Name=f"/platform/layers/{env}/{agent_name}/s3-key",
        Value="layers/echo-agent-deps-hash.zip",
        Type="String",
    )

    # Create fake pyproject.toml
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore.deployment]
type = "zip"
""")

    return agent_name, env, bucket_name


@mock_aws
def test_deploy_agent_zip_stores_runtime_arn_on_success(tmp_path, monkeypatch):
    agent_name, env, bucket_name = _prepare_deploy_agent_fixture(tmp_path, monkeypatch)
    _patch_deploy_agentcore(
        monkeypatch,
        _FakeAgentCoreClient(
            response={
                "agentArn": "arn:aws:bedrock-agentcore:eu-west-2:210987654321:runtime/echo-agent"
            }
        ),
    )

    assert deploy_agent.deploy_agent(agent_name, env) is True

    s3 = boto3.client("s3", region_name=_REGION)
    response = s3.list_objects_v2(Bucket=bucket_name)
    keys = [obj["Key"] for obj in response.get("Contents", [])]
    expected_script_key = f"scripts/{agent_name}/1.2.3.zip"
    assert expected_script_key in keys

    ssm = boto3.client("ssm", region_name=_REGION)
    s3_key_param = ssm.get_parameter(Name=f"/platform/agents/{env}/{agent_name}/script-s3-key")
    assert s3_key_param["Parameter"]["Value"] == expected_script_key
    runtime_arn_param = ssm.get_parameter(Name=f"/platform/agents/{env}/{agent_name}/runtime-arn")
    assert (
        runtime_arn_param["Parameter"]["Value"]
        == "arn:aws:bedrock-agentcore:eu-west-2:210987654321:runtime/echo-agent"
    )


@mock_aws
def test_deploy_agent_returns_false_when_runtime_update_fails(tmp_path, monkeypatch):
    agent_name, env, _ = _prepare_deploy_agent_fixture(tmp_path, monkeypatch)
    _patch_deploy_agentcore(
        monkeypatch,
        _FakeAgentCoreClient(error=RuntimeError("runtime update failed")),
    )

    assert deploy_agent.deploy_agent(agent_name, env) is False

    ssm = boto3.client("ssm", region_name=_REGION)
    s3_key_param = ssm.get_parameter(Name=f"/platform/agents/{env}/{agent_name}/script-s3-key")
    assert s3_key_param["Parameter"]["Value"] == f"scripts/{agent_name}/1.2.3.zip"
    with pytest.raises(ssm.exceptions.ParameterNotFound):
        ssm.get_parameter(Name=f"/platform/agents/{env}/{agent_name}/runtime-arn")


# ---------------------------------------------------------------------------
# register_agent.py tests
# ---------------------------------------------------------------------------


@mock_aws
def test_register_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_REGION", _REGION)
    monkeypatch.setenv("CI", "true")

    # Monkeypatch REPO_ROOT
    monkeypatch.setattr(register_agent, "REPO_ROOT", tmp_path)

    agent_name = "echo-agent"
    env = "dev"
    agent_dir = tmp_path / "agents" / agent_name
    agent_dir.mkdir(parents=True)
    (agent_dir / "pyproject.toml").write_text("""
[project]
name = "echo-agent"
version = "1.2.3"

[tool.agentcore]
owner_team = "platform"
tier_minimum = "basic"
invocation_mode = "sync"
""")

    # Setup DynamoDB
    dynamodb = boto3.client("dynamodb", region_name=_REGION)
    dynamodb.create_table(
        TableName="platform-agents",
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

    # Setup SSM values (environment-scoped)
    ssm = boto3.client("ssm", region_name=_REGION)
    ssm.put_parameter(
        Name=f"/platform/layers/{env}/{agent_name}/hash",
        Value="hash123",
        Type="String",
    )
    ssm.put_parameter(
        Name=f"/platform/layers/{env}/{agent_name}/s3-key", Value="layers/key.zip", Type="String"
    )
    ssm.put_parameter(
        Name=f"/platform/agents/{env}/{agent_name}/script-s3-key",
        Value="scripts/custom-key.zip",
        Type="String",
    )

    register_agent.register_agent(agent_name, env)

    # Verify DynamoDB record
    ddb = boto3.resource("dynamodb", region_name=_REGION)
    table = ddb.Table("platform-agents")
    response = table.get_item(Key={"PK": f"AGENT#{agent_name}", "SK": "VERSION#1.2.3"})
    item = response["Item"]
    assert item["agent_name"] == agent_name
    assert item["version"] == "1.2.3"
    assert item["layer_hash"] == "hash123"
    assert item["script_s3_key"] == "scripts/custom-key.zip"

    # Verify SSM latest-version (environment-scoped)
    param_name = f"/platform/agents/{env}/{agent_name}/latest-version"
    latest_version_param = ssm.get_parameter(Name=param_name)
    assert latest_version_param["Parameter"]["Value"] == "1.2.3"
